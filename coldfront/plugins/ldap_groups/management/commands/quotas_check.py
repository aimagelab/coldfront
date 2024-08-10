import logging
import os
import grp
from functools import lru_cache

from django.core.management.base import BaseCommand

from coldfront.core.resource.models import Resource
from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
from coldfront.plugins.ldap_groups.utils import (LDAP_NOOP,
                                             FILESYSTEM_ATTRIBUTE_NAME,
                                             UNIX_GROUP_ATTRIBUTE_NAME,
                                             STORAGE_QUOTA_ATTRIBUTE_NAME)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync quotas between Colfront and squota'

    def add_arguments(self, parser):
        parser.add_argument(
            "-s", "--sync", help="Sync changes to/from squota", action="store_true")
        parser.add_argument(
            "-n", "--noop", help="Print commands only. Do not run any commands.", action="store_true")
        parser.add_argument(
            "-x", "--header", help="Include header in output", action="store_true")

    # Cache squota output, we hypothesize it does not change during the execution of this script
    @lru_cache(maxsize=1)
    def get_from_squota(self, filesystem):
        command = "squota -f {} -A -P".format(filesystem)
        output = os.popen(command).read()

        quotas = dict()
        usages = dict()
        for line_i, line in enumerate(output.splitlines()):
            if line_i == 0:
                continue
            chunks = line.split('|')
            project, usage, quota = chunks[1], chunks[5], chunks[6]
            if quota != 'None':
                quotas[project] = float(quota)
            usages[project] = float(usage)

        return quotas, usages

    def set_quota(self, filesystem, group, quota):
        command = "squota -f {} -u {} -q {}".format(filesystem, group, quota)
        logger.info("Setting quota: %s", command)
        if not self.noop:
            os.system(command)

    def process_allocation(self, allocation):
        filesystem = allocation.resources.first().get_attribute(FILESYSTEM_ATTRIBUTE_NAME)
        storage_group_name = allocation.get_attribute(UNIX_GROUP_ATTRIBUTE_NAME)
        storage_quota = allocation.get_attribute(STORAGE_QUOTA_ATTRIBUTE_NAME)

        if storage_group_name is None:
            logger.warn("Skipping allocation %s as it does not have a storage group name", allocation)
            return
        
        # Create directory with sudo if it does not exist
        if not os.path.isdir(os.path.join(filesystem, storage_group_name)):
            command = "sudo mkdir {}".format(os.path.join(filesystem, storage_group_name))
            logger.info("Creating directory: %s", command)
            if not self.noop:
                os.system(command)
                logger.info("Created directory %s", os.path.join(filesystem, storage_group_name))

        # Change group ownership if it does not match
        if os.stat(os.path.join(filesystem, storage_group_name)).st_gid != grp.getgrnam(storage_group_name).gr_gid:
            command = "sudo chgrp {} {}".format(storage_group_name, os.path.join(filesystem, storage_group_name))
            if not self.noop:
                os.system(command)
            logger.info("Changed group ownership of %s to %s", os.path.join(filesystem, storage_group_name), storage_group_name)

        # Chmod 770 and g+s if it does not match
        if os.stat(os.path.join(filesystem, storage_group_name)).st_mode != 17912:
            command = "sudo chmod 2770 {}".format(os.path.join(filesystem, storage_group_name))
            if not self.noop:
                os.system(command)
            logger.info("Changed permissions of %s to 2770", os.path.join(filesystem, storage_group_name))

        # Set a default quota if it does not exist
        if storage_quota is None:
            logger.warn("Setting a default quota on allocation %s as it does not have a storage quota", allocation)
            storage_quota = 100

        # Get current quota and usage
        current_quota, current_usage = self.get_from_squota(filesystem)
        current_quota = current_quota.get(storage_group_name, None)
        current_usage = current_usage.get(storage_group_name, None)

        # Set quota if it does not match
        if current_quota is None or current_quota != storage_quota and self.sync:
            logger.warn("Setting quota on allocation %s to %s", allocation, storage_quota)
            self.set_quota(filesystem, storage_group_name, storage_quota)

        # Set usage if exists
        if current_usage is not None:
            # Round to 2 decimal places
            current_usage = round(current_usage, 2)
            logger.info("Setting usage on allocation %s to %s", allocation, current_usage)
            allocation.set_usage(STORAGE_QUOTA_ATTRIBUTE_NAME, current_usage)

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
            logger.warn("Syncing squota with ColdFront")

        resource = Resource.objects.get(pk=1) # Storage in WORK area resource
        allocations = Allocation.objects.filter(resources__in=[resource, ], status=AllocationStatusChoice.objects.get(name='Active')).distinct()
        logger.info("Processing %s active allocations", len(allocations))

        for allocation in allocations:
            self.process_allocation(allocation)
