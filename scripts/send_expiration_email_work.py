# Execute as Django script even when calling this file directly
import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "coldfront.config.settings")
django.setup()


from django.contrib.auth.models import User
from coldfront.core.allocation.models import Allocation
from datetime import datetime
from datetime import timedelta

# This script checks projects that have a WORK allocation that is going to expire, and sends an e-mail to all users 
# in the allocation to warn them that their allocation is about to expire. 

# Get all allocations of type WORK that are active and going to expire soon
allocations = []
for days in [60, 50, 40, 30, 20, 15, 10, 5, 4, 3, 2, 1]:
    this_allocations = Allocation.objects.filter(resources__pk=1, end_date=datetime.now() + timedelta(days=days), end_date__gte=datetime.now())
    allocations.extend(this_allocations)


for allocation in allocations:
    # Ask for confirmation before proceeding
    print(f"Project: {allocation.project.title}")
    print(f"WORK allocation: {allocation.end_date}")

    # Get all users in the allocation
    users = User.objects.filter(allocationuser__allocation=allocation).distinct()

    # Send e-mail to all users
    subject = f"[AImageLab-SRV] Your /work area for {allocation.project.title} is about to be deleted"
    message = f"Dear user,\n\nYour WORK allocation for project {allocation.project.title} is going to expire on {allocation.end_date.strftime('%Y-%m-%d')}, and is therefore about to be deleted.\n\nPlease, make sure to backup all your data before the expiration, or reach out at aimagelab-srv-support@unimore.it to require an extension.\n\nBest regards,\nAImageLab-SRV"

    for user in users:
        user.email_user(subject, message)
        print(f"Sent e-mail to {user.email} for project {allocation.project.title}")
