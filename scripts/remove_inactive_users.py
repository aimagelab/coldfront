"""
Interactive cleanup script: for each project, list inactive users (User.is_active=False)
attached to the project and/or its allocations. Optionally remove associations by
setting ProjectUser/AllocationUser statuses to 'Removed'. Keeps the User object.

Run with: python scripts/remove_inactive_users.py
"""

import os
import sys
import django


def setup_django():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "coldfront.config.settings")
    django.setup()


def main():
    from django.contrib.auth.models import User
    from coldfront.core.project.models import (
        Project,
        ProjectUser,
        ProjectUserStatusChoice,
    )
    from coldfront.core.allocation.models import (
        Allocation,
        AllocationUser,
        AllocationUserStatusChoice,
    )

    removed_project_status = ProjectUserStatusChoice.objects.get(name="Removed")
    removed_allocation_status = AllocationUserStatusChoice.objects.get(name="Removed")

    projects = Project.objects.all().order_by("title")
    if not projects.exists():
        print("No projects found.")
        return

    for project in projects:
        # Skip archived projects
        if project.status.name == "Archived":
            continue

        # Gather inactive users associated with project and/or its allocations
        inactive_proj_users = project.projectuser_set.filter(user__is_active=False).exclude(status=removed_project_status)
        inactive_alloc_users = AllocationUser.objects.filter(
            allocation__project=project, user__is_active=False
        ).exclude(status=removed_allocation_status)

        # Build unique set of impacted users
        impacted_user_ids = set(list(inactive_proj_users.values_list("user_id", flat=True)))
        impacted_user_ids.update(list(inactive_alloc_users.values_list("user_id", flat=True)))

        if not impacted_user_ids:
            continue

        users = User.objects.filter(id__in=impacted_user_ids).order_by("username")

        print("\n=== Project ===")
        print(f"Title: {project.title}")
        print(f"PI: {project.pi.username}")

        # Per-user attachment summary
        rows = []
        for user in users:
            in_project = inactive_proj_users.filter(user=user).exists()
            allocs_qs = Allocation.objects.filter(
                project=project, allocationuser__user=user
            ).distinct()
            allocs = [f"{a.id}:{a.get_parent_resource.name}" for a in allocs_qs]
            rows.append(
                {
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                    "in_project": in_project,
                    "allocations": allocs,
                }
            )

        print("Inactive users attached:")
        for r in rows:
            proj_flag = "project" if r["in_project"] else ""
            alloc_flag = f"allocations={len(r['allocations'])}" if r["allocations"] else ""
            flags = ", ".join([f for f in [proj_flag, alloc_flag] if f])
            print(
                f" - {r['username']} ({r['first_name']} {r['last_name']}) <{r['email']}> [{flags or 'none'}]"
            )

        # Ask to remove associations for all listed users in this project
        while True:
            resp = input(
                "Remove these associations (set status to 'Removed')? [y/N]: "
            ).strip().lower()
            if resp in ("y", "yes", "n", "no", ""):
                break

        if resp not in ("y", "yes"):
            print("Skipped.")
            continue

        removed_count_project = 0
        removed_count_alloc = 0

        for user in users:
            # Never remove PI from their project
            if user.id == project.pi_id:
                print(f" - Skipping PI {user.username}.")
                continue

            # Project association: set status to Removed if present and not already
            try:
                pu = ProjectUser.objects.get(project=project, user=user)
                if pu.status_id != removed_project_status.id:
                    pu.status = removed_project_status
                    pu.save()
                    removed_count_project += 1
            except ProjectUser.DoesNotExist:
                pass

            # Allocation associations within this project
            aus = AllocationUser.objects.filter(
                allocation__project=project, user=user
            )
            for au in aus:
                if au.status_id != removed_allocation_status.id:
                    au.status = removed_allocation_status
                    au.save()
                    removed_count_alloc += 1

        print(
            f"Done: project links removed={removed_count_project}, allocation links removed={removed_count_alloc}."
        )


if __name__ == "__main__":
    setup_django()
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
