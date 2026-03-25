#!/usr/bin/env /srv/coldfront/venv/bin/python3
"""
Interactive CLI script to create a new ColdFront project with:
- A WORK storage allocation (100 GB quota)
- A SLURM allocation (default hours based on project type)
- PI and member assignments
"""

import django
import os
import sys
from datetime import datetime, date

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "coldfront.config.settings")
django.setup()

from django.contrib.auth.models import User
from coldfront.core.project.models import (
    Project,
    ProjectStatusChoice,
    ProjectType,
    ProjectUser,
    ProjectUserRoleChoice,
    ProjectUserStatusChoice,
)
from coldfront.core.field_of_science.models import FieldOfScience
from coldfront.core.resource.models import Resource
from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttribute,
    AllocationAttributeType,
    AllocationStatusChoice,
    AllocationUser,
    AllocationUserStatusChoice,
)

# --- Configuration ---

# Default WORK quota in GB
DEFAULT_WORK_QUOTA_GB = 100


# --- Helpers ---

def prompt(text, default=None):
    """Prompt user for input with an optional default."""
    if default:
        raw = input(f"{text} [{default}]: ").strip()
        return raw if raw else default
    while True:
        raw = input(f"{text}: ").strip()
        if raw:
            return raw
        print("  This field is required.")


def prompt_choice(text, choices, allow_empty=False):
    """
    Display numbered choices and let the user pick one.
    `choices` is a list of (value, label) tuples.
    Returns the selected value.
    """
    print(f"\n{text}")
    for i, (value, label) in enumerate(choices, 1):
        print(f"  {i}) [{value}] {label}")
    while True:
        raw = input("Enter number: ").strip()
        if allow_empty and raw == "":
            return None
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx][0]
        except ValueError:
            pass
        print("  Invalid selection, try again.")


def prompt_date(text, default=None):
    """Prompt for a date in YYYY-MM-DD format."""
    while True:
        raw = prompt(text, default=default)
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            print("  Invalid date format. Use YYYY-MM-DD.")


def lookup_user(label="Username"):
    """Look up a Django user by username."""
    while True:
        username = prompt(label)
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            print(f"  User '{username}' not found. Try again.")


def lookup_users_multi(label="Usernames (comma-separated, empty to skip)"):
    """Look up multiple Django users by username."""
    raw = input(f"{label}: ").strip()
    if not raw:
        return []
    usernames = [u.strip() for u in raw.split(",") if u.strip()]
    users = []
    for uname in usernames:
        try:
            users.append(User.objects.get(username=uname))
        except User.DoesNotExist:
            print(f"  Warning: user '{uname}' not found, skipping.")
    return users


# --- Main ---

def main():
    print("=" * 60)
    print("  Create a new ColdFront project")
    print("=" * 60)

    # 1. Project title
    title = prompt("Project title")

    # 2. Project type
    active_types = list(ProjectType.objects.filter(active=True).order_by("code"))
    if not active_types:
        print("Error: no active project types found in the database.")
        sys.exit(1)
    type_choices = [(pt.code, pt.name) for pt in active_types]
    project_type_code = prompt_choice("Select project type:", type_choices)
    project_type_obj = next(pt for pt in active_types if pt.code == project_type_code)

    # 3. PI
    print("\n--- Principal Investigator ---")
    pi = lookup_user("PI username")

    # Check uniqueness
    if Project.objects.filter(title=title, pi=pi).exists():
        print(f"\nError: A project '{title}' with PI '{pi.username}' already exists.")
        sys.exit(1)

    # 4. Field of science
    print("\n--- Field of Science ---")
    fos_query = prompt("Search field of science (keyword)").strip()
    fos_matches = FieldOfScience.objects.filter(description__icontains=fos_query).order_by("description")
    if not fos_matches.exists():
        print(f"  No results for '{fos_query}'. Using first available.")
        field_of_science = FieldOfScience.objects.order_by("pk").first()
    elif fos_matches.count() == 1:
        field_of_science = fos_matches.first()
        print(f"  Selected: {field_of_science.description}")
    else:
        fos_choices = [(str(f.pk), f.description) for f in fos_matches[:20]]
        if fos_matches.count() > 20:
            print(f"  Showing first 20 of {fos_matches.count()} results. Refine your search if needed.")
        fos_pk = prompt_choice("Select field of science:", fos_choices)
        field_of_science = FieldOfScience.objects.get(pk=int(fos_pk))

    # 5. Description
    description = prompt("Short project description (>=10 chars)", default=Project.DEFAULT_DESCRIPTION.strip())

    # 6. End date for allocations
    default_end = f"{date.today().year + 1}-01-01"
    end_date = prompt_date("Allocation end date (YYYY-MM-DD)", default=default_end)
    start_date = date.today()

    # 7. Custom SLURM budget
    default_budget = project_type_obj.annual_budget
    budget_input = prompt(f"SLURM budget in standard hours", default=str(default_budget))
    try:
        budget = int(budget_input)
    except ValueError:
        print(f"  Invalid number, using default ({default_budget}).")
        budget = default_budget

    # 8. Members
    print("\n--- Project members (besides PI) ---")
    members = lookup_users_multi("Member usernames (comma-separated, empty to skip)")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)
    print(f"  Title           : {title}")
    print(f"  Type            : [{project_type_obj.code}] {project_type_obj.name}")
    print(f"  PI              : {pi.username} ({pi.first_name} {pi.last_name})")
    print(f"  Members         : {', '.join(m.username for m in members) or '(none)'}")
    print(f"  Field of Science: {field_of_science.description}")
    print(f"  Description     : {description[:60]}{'...' if len(description) > 60 else ''}")
    print(f"  Allocation dates: {start_date} -> {end_date}")
    print(f"  WORK quota      : {DEFAULT_WORK_QUOTA_GB} GB")
    print(f"  SLURM budget    : {budget} standard hours")
    print("=" * 60)

    confirm = input("\nProceed? (y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    # ============================
    #  Create objects
    # ============================

    # -- Project --
    project_status = ProjectStatusChoice.objects.get(name="Active")
    project = Project.objects.create(
        title=title,
        pi=pi,
        description=description,
        status=project_status,
        project_type=project_type_obj,
        field_of_science=field_of_science,
    )
    print(f"\n✓ Project '{project.title}' created (pk={project.pk}).")

    # -- PI as Manager in ProjectUser --
    pi_role = ProjectUserRoleChoice.objects.get(name="Manager")
    pi_status = ProjectUserStatusChoice.objects.get(name="Active")
    ProjectUser.objects.create(project=project, user=pi, role=pi_role, status=pi_status)
    print(f"✓ PI '{pi.username}' added as Manager.")

    # -- Members --
    member_role = ProjectUserRoleChoice.objects.get(name="User")
    for member in members:
        ProjectUser.objects.get_or_create(
            project=project,
            user=member,
            defaults={"role": member_role, "status": pi_status},
        )
        print(f"✓ Member '{member.username}' added.")

    # -- Allocation status --
    alloc_status = AllocationStatusChoice.objects.get(name="Active")
    alloc_user_status = AllocationUserStatusChoice.objects.get(name="Active")

    # ======================
    #  WORK allocation
    # ======================
    work_resource = Resource.objects.get(name="Storage in WORK area")
    work_alloc = Allocation.objects.create(
        project=project,
        status=alloc_status,
        start_date=start_date,
        end_date=end_date,
        justification="Automatically created by add_project script.",
        is_locked=False,
        is_changeable=True,
    )
    work_alloc.resources.add(work_resource)

    # Storage_Group_Name
    AllocationAttribute.objects.create(
        allocation=work_alloc,
        allocation_attribute_type=AllocationAttributeType.objects.get(name="Storage_Group_Name"),
        value=title,
    )
    # Storage Quota (GB)
    AllocationAttribute.objects.create(
        allocation=work_alloc,
        allocation_attribute_type=AllocationAttributeType.objects.get(name="Storage Quota (GB)"),
        value=str(DEFAULT_WORK_QUOTA_GB),
    )

    # Add PI and members to WORK allocation
    AllocationUser.objects.create(allocation=work_alloc, user=pi, status=alloc_user_status)
    for member in members:
        AllocationUser.objects.get_or_create(
            allocation=work_alloc,
            user=member,
            defaults={"status": alloc_user_status},
        )

    print(f"✓ WORK allocation created (pk={work_alloc.pk}), quota={DEFAULT_WORK_QUOTA_GB} GB.")

    # ======================
    #  SLURM allocation
    # ======================
    slurm_resource = Resource.objects.get(name="SLURM account")
    slurm_alloc = Allocation.objects.create(
        project=project,
        status=alloc_status,
        start_date=start_date,
        end_date=end_date,
        justification="Automatically created by add_project script.",
        is_locked=False,
        is_changeable=True,
    )
    slurm_alloc.resources.add(slurm_resource)

    # slurm_account_name
    AllocationAttribute.objects.create(
        allocation=slurm_alloc,
        allocation_attribute_type=AllocationAttributeType.objects.get(name="slurm_account_name"),
        value=title,
    )
    # slurm_budget
    AllocationAttribute.objects.create(
        allocation=slurm_alloc,
        allocation_attribute_type=AllocationAttributeType.objects.get(name="slurm_budget"),
        value=str(budget),
    )

    # Add PI and members to SLURM allocation
    AllocationUser.objects.create(allocation=slurm_alloc, user=pi, status=alloc_user_status)
    for member in members:
        AllocationUser.objects.get_or_create(
            allocation=slurm_alloc,
            user=member,
            defaults={"status": alloc_user_status},
        )

    print(f"✓ SLURM allocation created (pk={slurm_alloc.pk}), budget={budget} standard hours.")

    print("\n✓ Done! Project and allocations are ready.")


if __name__ == "__main__":
    main()
