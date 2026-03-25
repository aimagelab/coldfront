# Execute as Django script even when calling this file directly
import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "coldfront.config.settings")
django.setup()


from coldfront.core.project.models import Project, ProjectStatusChoice
from coldfront.core.allocation.models import (
                                            Allocation,
                                            )
from datetime import datetime, timedelta

# This script checks projects for which all allocations are in status "Expired" or "Expired and data deleted",
# and archives them.

arhived_status = ProjectStatusChoice.objects.get(name='Archived')
projects = Project.objects.all()

for project in projects:
    if project.status == arhived_status:
        continue  # Skip already archived projects

    allocations = Allocation.objects.filter(project=project)

    # Check if all allocations are expired by at least six months
    all_expired = True
    for allocation in allocations:
        if allocation.end_date is None or allocation.end_date > (datetime.now() - timedelta(days=180)).date() or allocation.status.name not in ['Expired', 'Expired and data deleted']:
            all_expired = False
            break
        
    if all_expired and allocations.exists():
        # Ask for confirmation before proceeding
        print(f"Project: {project.title}")
        print(f"Current status: {project.status.name}")
        print("All allocations are expired. Do you want to archive this project? (y/n)")
        answer = input()

        if answer != 'y':
            print("Skipping...")
            continue

        # Archive the project
        project.status = arhived_status
        project.save()

        print(f"Project '{project.title}' has been archived.")