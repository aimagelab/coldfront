import logging
import datetime
import pwd
import os
import subprocess
import time

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.core.mail import EmailMessage

from coldfront.core.portal.models import AccountOnboardingRequest
from coldfront.plugins.ldap_groups.ldap_connector import LDAP
from coldfront.plugins.ldap_groups.utils import (
    ACCESS_GROUPS,
    ROLE_GROUPS_MAP,
    make_sha_password,
    random_password,
)

logger = logging.getLogger(__name__)

DEFAULT_HOME_ROOT = getattr(settings, 'LDAP_HOME_ROOT', '/homes')
HOME_QUOTA_GB = getattr(settings, 'LDAP_HOME_QUOTA_GB', 100)


class Command(BaseCommand):
    help = 'Provision approved AccountOnboardingRequest objects into LDAP (create/update).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Do not write to LDAP or mark processed.')
        parser.add_argument('--age-minutes', type=int, default=15, help='Minimum age in minutes since approval (processed_at).')
        parser.add_argument('--limit', type=int, default=100, help='Max requests to process in one run.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        age_minutes = options['age_minutes']
        limit = options['limit']

        cutoff = timezone.now() - datetime.timedelta(minutes=age_minutes)

        qs = (AccountOnboardingRequest.objects
              .filter(status=AccountOnboardingRequest.STATUS_APPROVED,
                      processed_in_ldap=False,
                      processed_at__lte=cutoff)
              .order_by('processed_at')[:limit])

        count = qs.count()
        if count == 0:
            self.stdout.write('No approved onboarding requests pending LDAP provisioning.')
            return

        self.stdout.write(f'Processing {count} onboarding request(s)...')
        ldap_client = LDAP()

        for req in qs:
            try:
                self._process_request(req, ldap_client, dry_run=dry_run)
            except Exception as e:
                logger.exception('Failed processing request %s: %s', req.id, e)
                continue

    def _process_request(self, req: AccountOnboardingRequest, ldap_client: LDAP, dry_run: bool = False):
        role_group = ROLE_GROUPS_MAP.get(req.role)
        if not role_group:
            logger.warning('Skipping request %s: unmapped role %s', req.id, req.role)
            return
        expiration_date = req.expiration_date

        # Determine if user exists in LDAP
        exists = ldap_client.user_exists(req.username)
        logger.info('Request %s -> username=%s exists=%s', req.id, req.username, exists)

        # Decide password scheme: UNIMORE accounts detected by presence of unimore_id
        raw_pw = None  # store one-time password for non-UNIMORE accounts
        is_unimore = bool(req.unimore_id)
        unimore_ldap_username = req.unimore_id if is_unimore else None

        if is_unimore:
            password_hash = '{SASL}' + unimore_ldap_username
        else:
            raw_pw = random_password()
            password_hash = make_sha_password(raw_pw)

        if not exists:
            # Create new user
            if dry_run:
                logger.info('[DRY-RUN] Would create user %s', req.username)
            else:
                ldap_client.create_user(
                    username=req.username,
                    first_name=req.given_name,
                    last_name=req.surname,
                    email=req.email,
                    role=role_group,
                    password_hash=password_hash,
                    expiration_date=expiration_date,
                    is_unimore=is_unimore,
                    unimore_ldap_username=unimore_ldap_username,
                    codice_fiscale=req.codice_fiscale,
                    home_root=DEFAULT_HOME_ROOT,
                )
        else:
            # Update existing user attributes using symmetric API
            is_unimore = bool(req.unimore_id)
            unimore_ldap_username = req.unimore_id if is_unimore else None
            if dry_run:
                logger.info('[DRY-RUN] Would update user %s', req.username)
            else:
                ldap_client.update_user(
                    username=req.username,
                    first_name=req.given_name,
                    last_name=req.surname,
                    email=req.email,
                    role=role_group,
                    password_hash=password_hash,  # restore SASL ref or set new OTP on renewal
                    expiration_date=expiration_date,
                    is_unimore=is_unimore,
                    unimore_ldap_username=unimore_ldap_username,
                    codice_fiscale=req.codice_fiscale,
                    move_if_role_changed=True,
                )
                # Reactivate Django User if it exists and was marked inactive
                try:
                    django_user = User.objects.filter(username=req.username).first()
                    if django_user and not django_user.is_active:
                        django_user.is_active = True
                        django_user.save(update_fields=['is_active'])
                        logger.info('Reactivated Django user %s', req.username)
                except Exception as e:
                    logger.warning('Failed to reactivate Django user %s: %s', req.username, e)

        if not dry_run:
            ldap_client.add_user_to_groups(req.username, ACCESS_GROUPS)
            ldap_client.add_user_to_groups(req.username, [role_group])
            self._ensure_home_directory(req.username)
            self._send_welcome_email(req, raw_pw)

        if not dry_run:
            # Mark request as processed in LDAP
            req.processed_in_ldap = True
            req.save(update_fields=['processed_in_ldap'])
            logger.info('Request %s marked processed_in_ldap', req.id)
        else:
            logger.info('[DRY-RUN] Skipping marking processed_in_ldap for request %s', req.id)

    # ------------------------------------------------------------------
    # Email helpers
    # ------------------------------------------------------------------
    def _load_welcome_template(self) -> str:
        """Load the welcome template from email.txt (three %s placeholders)."""
        path = os.path.join(os.path.dirname(__file__), 'email.txt')
        try:
            with open(path, 'r') as f:
                return f.read()
        except Exception as e:
            logger.error('Failed to read welcome email template at %s: %s', path, e)
            return ('Dear %s,\n\nYour account "%s" has been created. Expiration: %s. '
                    'If you are a non-UNIMORE user you should have received a separate password email.')

    def _send_welcome_email(self, req: AccountOnboardingRequest, raw_pw: str | None):
        center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
        template = self._load_welcome_template()
        template = template.replace('{center_name}', center_name)
        expiration_str = req.expiration_date.strftime('%Y-%m-%d') if req.expiration_date else 'N/A'
        try:
            body = template % (req.given_name, req.username, expiration_str)
        except Exception:
            # fallback if formatting fails
            body = template

        from_email = getattr(settings, 'EMAIL_SENDER')
        # Always send welcome
        try:
            EmailMessage(
                subject=f'Welcome to {center_name}!',
                body=body,
                from_email=from_email,
                to=[req.email]
            ).send(fail_silently=False)
            logger.info('Sent welcome email to %s', req.email)
        except Exception as e:
            logger.error('Failed to send welcome email to %s: %s', req.email, e)

        # OTP email for non-UNIMORE accounts only
        if raw_pw:
            otp_body = (
                f'Dear {req.given_name},\n\n'
                f'Your one-time password for {center_name} is:\n\n'
                f'  {raw_pw}\n\n'
                f'Use this password at your first SSH login from a University IP address '
                f'(on-campus network, WiFi, or VPN). You will be prompted to change it '
                f'immediately after logging in.\n\n'
                f'The {center_name} Team'
            )
            try:
                EmailMessage(
                    subject=f'One-time password for {center_name}',
                    body=otp_body,
                    from_email=from_email,
                    to=[req.email]
                ).send(fail_silently=False)
                logger.info('Sent OTP email to %s', req.email)
            except Exception as e:
                logger.error('Failed to send OTP email to %s: %s', req.email, e)

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------
    def _wait_for_nss(self, username: str, timeout: int = 30, interval: int = 2):
        """Wait until username is resolvable via NSS (i.e. nscd has picked up the new LDAP entry)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                pwd.getpwnam(username)
                return True
            except KeyError:
                time.sleep(interval)
        logger.warning('User %s not visible in NSS after %ds — proceeding anyway', username, timeout)
        return False

    def _ensure_home_directory(self, username: str):
        """Create and initialize the user's home directory if it does not exist.
        Mirrors the minimal behavior from legacy add_user.py (mkdir, populate, quota).
        Uses sudo for privileged commands; assumes proper sudoers config.
        """
        self._wait_for_nss(username)
        home_path = os.path.join(DEFAULT_HOME_ROOT, username)
        if os.path.exists(home_path):
            logger.debug('Home directory already exists for %s', username)
        else:
            logger.info('Creating home directory for %s', username)
            script_path = os.path.join(os.path.dirname(__file__), 'populate_home.sh')
            script_dir = os.path.dirname(script_path)
            if os.path.exists(script_path):
                try:
                    subprocess.run(
                        ['sudo', script_path, username, ACCESS_GROUPS[0] if ACCESS_GROUPS else 'ailb-srv'],
                        check=True,
                        cwd=script_dir
                    )
                    logger.info('Populated home directory for %s using %s', username, script_path)
                except Exception as e:
                    logger.warning('Failed to populate home directory for %s: %s', username, e)
            else:
                logger.warning('populate_home script not found at %s; home directory left unpopulated.', script_path)
        # Set quota (using squota tool if available)
        try:
            subprocess.run(['sudo', '/usr/local/bin/squota', '-u', username, '-f', DEFAULT_HOME_ROOT, '-q', str(HOME_QUOTA_GB)], check=True)
        except Exception as e:
            logger.warning('Failed to set quota for %s: %s', username, e)

