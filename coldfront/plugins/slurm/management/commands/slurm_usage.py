import logging
import os
import grp
from functools import lru_cache

from django.core.management.base import BaseCommand

from coldfront.core.resource.models import Resource
from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
from coldfront.plugins.slurm.utils import SLURM_ACCOUNT_ATTRIBUTE_NAME, SLURM_BUDGET_ATTRIBUTE_NAME

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Update usages from susage to Colfront'

    def add_arguments(self, parser):
        parser.add_argument(
            "-s", "--sync", help="Sync changes to/from susage", action="store_true")
        parser.add_argument(
            "-n", "--noop", help="Print commands only. Do not run any commands.", action="store_true")
        parser.add_argument(
            "-x", "--header", help="Include header in output", action="store_true")

    # Cache susage output, we hypothesize it does not change during the execution of this script
    @lru_cache(maxsize=1)
    def get_from_susage(self):
        command = "susage -P"
        output = os.popen(command).read()

        usages = dict()
        for line_i, line in enumerate(output.splitlines()):
            if line_i == 0:
                continue
            chunks = line.split('|')
            project, _, usage = chunks[:3]
            usages[project] = float(usage)

        return usages

    def process_allocation(self, allocation):
        account = allocation.get_attribute(SLURM_ACCOUNT_ATTRIBUTE_NAME)

        if account is None:
            logger.warn("Skipping allocation %s as it does not have an account name", allocation)
            return
        
        # Get current usage
        current_usage = self.get_from_susage().get(account, None)

        # Set usage if exists
        if current_usage is not None:
            # Round to 2 decimal places
            current_usage = round(current_usage, 2)
            logger.info("Setting usage on allocation %s to %s", allocation, current_usage)
            allocation.set_usage(SLURM_BUDGET_ATTRIBUTE_NAME, current_usage)

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

        self.noop = False
        if options['noop']:
            self.noop = True
            logger.warn("NOOP enabled")

        self.sync = False
        if options['sync']:
            self.sync = True
            logger.warn("Syncing susage with ColdFront")

        resource = Resource.objects.get(pk=2) # SLURM resource
        allocations = Allocation.objects.filter(resources__in=[resource, ], status=AllocationStatusChoice.objects.get(name='Active')).distinct()
        logger.info("Processing %s active allocations", len(allocations))

        for allocation in allocations:
            self.process_allocation(allocation)
