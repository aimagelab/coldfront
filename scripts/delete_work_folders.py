# Execute as Django script even when calling this file directly
import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "coldfront.config.settings")
django.setup()


from django.db.models import Q
from django.contrib.auth.models import User
from coldfront.core.allocation.models import (
                                            Allocation,
                                            AllocationAdminNote,
                                            AllocationAttribute,
                                            AllocationStatusChoice,
                                            )
from datetime import datetime

# This script checks projects that have an expired WORK allocations, 
# deletes the corresponding folder in the filesystem, and
# it expires it in Coldfront and sets its status to "Expired and data deleted". 

# Get user lbaraldi
user = User.objects.get(username='lbaraldi')

# Get all expired allocations of type WORK
allocations = Allocation.objects.filter(end_date__lt=datetime.now(), resources__pk=1, status__name='Expired').distinct()

for allocation in allocations:
    # Ask for confirmation before proceeding
    print(f"Project: {allocation.project.title}")
    print(f"WORK allocation: {allocation.end_date}")
    print("Do you want to proceed? (y/n)")
    answer = input()

    if answer != 'y':
        print("Skipping...")
        continue

    # Get allocation attribute "Storage_Group_Name"
    storage_group_name = AllocationAttribute.objects.filter(allocation=allocation, allocation_attribute_type__name="Storage_Group_Name").first().value
    work_path = os.path.join("/work", storage_group_name)

    # Create an empty temporary folder, then execute rsync -a --delete /tmp/empty/ /work/storage_group_name as root
    # to delete the folder in the filesystem
    os.system(f"mkdir /tmp/empty")
    os.system(f"sudo rsync -a --delete /tmp/empty/ {work_path}")
    os.system(f"sudo rm -rf {work_path}")
    os.system(f"rm -rf /tmp/empty")

    # Expire the allocation and set status to "Expired and data deleted"
    allocation.status = AllocationStatusChoice.objects.get(name='Expired and data deleted')
    allocation.save()

    # Create AdminNote to record the deletion
    note = AllocationAdminNote.objects.create(allocation=allocation, author=user, note='Deleted folder in WORK area.')
    note.save()

