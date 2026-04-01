import logging
import datetime
import hashlib
import random
import string
import os
import subprocess
from base64 import b64encode

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.core.mail import EmailMessage

from django.contrib.auth.models import User

from coldfront.core.portal.models import AccountOnboardingRequest
from coldfront.core.project.models import (
    Project,
    ProjectUser,
    ProjectUserRoleChoice,
    ProjectUserStatusChoice,
)
from coldfront.core.project.signals import project_activate_user
from coldfront.core.allocation.models import (
    Allocation,
    AllocationUser,
    AllocationUserStatusChoice,
)
from coldfront.core.allocation.signals import allocation_activate_user
from coldfront.plugins.ldap_groups.ldap_connector import LDAP

logger = logging.getLogger(__name__)

ACCESS_GROUPS = getattr(settings, 'LDAP_ACCESS_GROUPS', ['ailb-srv'])
ROLE_GROUPS_MAP = getattr(settings, 'LDAP_ROLE_GROUPS_MAP', {
    'Studente di Dottorato': 'dottorandi',
    'Assegno di Ricerca': 'assegnisti',
    'Contratto di Ricerca': 'contratti_ricerca',
    'Incarico di Ricerca': 'incarichi_ricerca',
    'Incarico Post-Doc': 'incarichi_postdoc',
    'Incarico di Collaborazione': 'collaborazioni',
    'Ricercatore RTD-A': 'ricercatori_rtda',
    'Ricercatore RTD-B': 'ricercatori_rtd',
    'Ricercatore RTT': 'ricercatori_rtt',
    'Professore Associato': 'professori_associati',
    'Professore Ordinario': 'professori_ordinari',
    'Studente in tesi': 'tesisti',
    'Studente da un corso': 'studenti',
    'Ospiti a vario titolo': 'ospiti',
    'Deactivated user': 'past_members',
})
DEFAULT_HOME_ROOT = getattr(settings, 'LDAP_HOME_ROOT', '/homes')
HOME_QUOTA_GB = getattr(settings, 'LDAP_HOME_QUOTA_GB', 100)
PASSWORD_CHARS = string.ascii_letters + string.digits + '!?._-'


def make_sha_password(raw: str) -> str:
    sha = hashlib.sha1()
    sha.update(raw.encode())
    return '{SHA}' + b64encode(sha.digest()).decode()


def random_password(length: int = 12) -> str:
    return ''.join(random.choice(PASSWORD_CHARS) for _ in range(length))


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
            if req.role == AccountOnboardingRequest.ROLE_COURSE and req.course_project_id:
                self._add_user_to_course_project(req)

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
    def _ensure_home_directory(self, username: str):
        """Create and initialize the user's home directory if it does not exist.
        Mirrors the minimal behavior from legacy add_user.py (mkdir, populate, quota).
        Uses sudo for privileged commands; assumes proper sudoers config.
        """
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
            subprocess.run(['sudo', '/usr/local/bin/squota', '-u', username, '-f', DEFAULT_HOME_ROOT, '-q', str(HOME_QUOTA_GB)], check=False)
        except Exception as e:
            logger.warning('Failed to set quota for %s: %s', username, e)

    # ------------------------------------------------------------------
    # Course project helpers
    # ------------------------------------------------------------------
    def _add_user_to_course_project(self, req: AccountOnboardingRequest):
        """Add the newly provisioned course student to their course project and all its allocations."""
        try:
            user_obj, _ = User.objects.get_or_create(username=req.username)
            user_obj.first_name = req.given_name
            user_obj.last_name = req.surname
            user_obj.email = req.email
            user_obj.save()

            project = req.course_project
            user_role = ProjectUserRoleChoice.objects.get(name='User')
            active_project_status = ProjectUserStatusChoice.objects.get(name='Active')
            active_alloc_user_status = AllocationUserStatusChoice.objects.get(name='Active')

            # Add to project
            if project.projectuser_set.filter(user=user_obj).exists():
                project_user_obj = project.projectuser_set.get(user=user_obj)
                project_user_obj.role = user_role
                project_user_obj.status = active_project_status
                project_user_obj.save()
            else:
                project_user_obj = ProjectUser.objects.create(
                    user=user_obj,
                    project=project,
                    role=user_role,
                    status=active_project_status,
                )
            project_activate_user.send(sender=self.__class__, project_user_pk=project_user_obj.pk)

            # Add to all active allocations of the project
            active_alloc_statuses = ['Active', 'Renewal Requested']
            for allocation in project.allocation_set.filter(status__name__in=active_alloc_statuses):
                if allocation.allocationuser_set.filter(user=user_obj).exists():
                    alloc_user_obj = allocation.allocationuser_set.get(user=user_obj)
                    alloc_user_obj.status = active_alloc_user_status
                    alloc_user_obj.save()
                else:
                    alloc_user_obj = AllocationUser.objects.create(
                        allocation=allocation,
                        user=user_obj,
                        status=active_alloc_user_status,
                    )
                allocation_activate_user.send(sender=self.__class__, allocation_user_pk=alloc_user_obj.pk)

            logger.info('Added user %s to course project %s', req.username, project.pk)
        except Exception as e:
            logger.error('Failed to add user %s to course project %s: %s', req.username, req.course_project_id, e)
