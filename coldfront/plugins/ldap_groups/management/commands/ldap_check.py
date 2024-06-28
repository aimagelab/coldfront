import logging
import os
import sys

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from coldfront.plugins.ldap_groups.ldap_connector import LDAP
from coldfront.core.allocation.models import AllocationUser
from coldfront.plugins.ldap_groups.utils import (LDAP_NOOP,
                                             UNIX_GROUP_ATTRIBUTE_NAME,
                                             AlreadyMemberError,
                                             NotMemberError)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync groups in LDAP'

    def __init__(self):
        super().__init__()
        self.ldap = LDAP()

    def add_arguments(self, parser):
        parser.add_argument(
            "-s", "--sync", help="Sync changes to/from LDAP", action="store_true")
        parser.add_argument("-u", "--username", help="Check specific username")
        parser.add_argument("-g", "--group", help="Check specific group")
        parser.add_argument(
            "-n", "--noop", help="Print commands only. Do not run any commands.", action="store_true")
        parser.add_argument(
            "-x", "--header", help="Include header in output", action="store_true")

    def write(self, data):
        try:
            self.stdout.write(data)
        except BrokenPipeError:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            sys.exit(1)

    def add_group(self, user, group, status):
        if self.sync and not self.noop:
            try:
                self.ldap.group_add_member(group, user=[user.username])
            except AlreadyMemberError as e:
                logger.warn("User %s is already a member of group %s",
                            user.username, group)
            except Exception as e:
                logger.error("Failed adding user %s to group %s: %s",
                             user.username, group, e)
            else:
                logger.info("Added user %s to group %s successfully",
                            user.username, group)

        row = [
            user.username,
            group,
            '',
            status,
            'Active' if user.is_active else 'Inactive',
        ]

        self.write('\t'.join(row))

    def remove_group(self, user, group, status):
        if self.sync and not self.noop:
            try:
                self.ldap.group_remove_member(
                    group, user=[user.username])
            except NotMemberError as e:
                logger.warn("User %s is not a member of group %s",
                            user.username, group)
            except Exception as e:
                logger.error(
                    "Failed removing user %s from group %s: %s", user.username, group, e)
            else:
                logger.info(
                    "Removed user %s from group %s successfully", user.username, group)

        row = [
            user.username,
            '',
            group,
            status,
            'Active' if user.is_active else 'Inactive',
        ]

        self.write('\t'.join(row))

    def sync_user_status(self, user, active=False):
        if not self.sync:
            return

        if self.noop:
            return

        try:
            user.is_active = active
            user.save()
        except Exception as e:
            logger.error('Failed to update user status: %s - %s',
                         user.username, e)

    def check_user(self, user, active_groups, removed_groups):
        logger.info("Checking user=%s active_groups=%s removed_groups=%s", user.username, active_groups, removed_groups)

        groups = []
        status = 'Unknown'
        try:
            groups = self.ldap.get_groups_of_user(user.username)
            if 'past_members' not in groups:
                status = 'Enabled'
            else:
                status = 'Disabled'
        except Exception as e:
            logger.warn("User %s not found in LDAP", user.username)
            status = 'NotFound'
            return

        if status == 'Disabled' and user.is_active:
            logger.warn(
                'User is active in coldfront but disabled in LDAP: %s', user.username)
            self.sync_user_status(user, active=False)
        elif status == 'Enabled' and not user.is_active:
            logger.warn(
                'User is not active in coldfront but enabled in LDAP: %s', user.username)
            self.sync_user_status(user, active=True)

        for g in active_groups:
            if g not in groups:
                logger.warn(
                    'User %s should be added to LDAP group: %s', user.username, g)
                self.add_group(user, g, status)

        for g in removed_groups:
            if g in groups:
                logger.warn(
                    'User %s should be removed from LDAP group: %s', user.username, g)
                self.remove_group(user, g, status)

        # Lastly, update user e-mail from LDAP
        try:
            user.email = self.ldap.get_email(user.username)
            user.save()
        except Exception as e:
            logger.error('Failed to update user e-mail: %s - %s',
                         user.username, e)

    def process_user(self, user):
        if self.filter_user and self.filter_user != user.username:
            return

        user_allocations = AllocationUser.objects.filter(
            user=user,
            allocation__allocationattribute__allocation_attribute_type__name=UNIX_GROUP_ATTRIBUTE_NAME
        )

        active_groups = []
        for ua in user_allocations:
            if ua.status.name != 'Active':
                logger.debug("Skipping inactive allocation to %s for user %s", ua.allocation.get_resources_as_string, user.username)
                continue

            if ua.allocation.status.name != 'Active':
                logger.debug("Skipping allocation to %s for user %s because they are not an active user", ua.allocation.get_resources_as_string, user.username)
                continue

            all_resources_inactive = True
            for r in ua.allocation.resources.all():
                if r.is_available:
                    all_resources_inactive = False

            if all_resources_inactive:
                logger.debug("Skipping allocation to %s for user %s due to all resources being inactive", ua.allocation.get_resources_as_string, user.username)
                continue

            for g in ua.allocation.get_attribute_list(UNIX_GROUP_ATTRIBUTE_NAME):
                if g not in active_groups:
                    active_groups.append(g)

        removed_groups = []
        for ua in user_allocations:
            if ua.status.name == 'Active' and ua.allocation.status.name == 'Active':
                continue

            # XXX Skip new or renewal allocations??
            if ua.allocation.status.name == 'New' or ua.allocation.status.name == 'Renewal Requested':
                continue

            for g in ua.allocation.get_attribute_list(UNIX_GROUP_ATTRIBUTE_NAME):
                if g not in removed_groups and g not in active_groups:
                    removed_groups.append(g)

        if self.filter_group:
            if self.filter_group in active_groups:
                active_groups = [self.filter_group]
            else:
                active_groups = []

            if self.filter_group in removed_groups:
                removed_groups = [self.filter_group]
            else:
                removed_groups = []

        if len(active_groups) == 0 and len(removed_groups) == 0:
            return

        self.check_user(user, active_groups, removed_groups)

    def handle(self, *args, **options):
        verbosity = int(options['verbosity'])
        root_logger = logging.getLogger('')
        if verbosity == 0:
            root_logger.setLevel(logging.ERROR)
        elif verbosity == 2:
            root_logger.setLevel(logging.INFO)
        elif verbosity == 3:
            root_logger.setLevel(logging.DEBUG)
        else:
            root_logger.setLevel(logging.WARN)

        self.noop = LDAP_NOOP
        if options['noop']:
            self.noop = True
            logger.warn("NOOP enabled")

        self.sync = False
        if options['sync']:
            self.sync = True
            logger.warn("Syncing LDAP with ColdFront")

        header = [
            'username',
            'add_missing_group_membership',
            'remove_existing_group_membership',
            'ldap_status',
            'coldfront_status',
        ]

        if options['header']:
            self.write('\t'.join(header))

        users = User.objects.all()
        logger.info("Processing %s active users", len(users))

        self.filter_user = ''
        self.filter_group = ''
        if options['username']:
            logger.info("Filtering output by username: %s",
                        options['username'])
            self.filter_user = options['username']
        if options['group']:
            logger.info("Filtering output by group: %s", options['group'])
            self.filter_group = options['group']

        for user in users:
            self.process_user(user)
