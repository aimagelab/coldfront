"""
Interactive PI change tool: select projects by title and optionally change
the PI per project.

- Accepts a project title as input (argument or prompt)
- Lists each matching Active project and asks for confirmation
- Prompts for the new PI username on confirmation
- Validates the user exists and is active (and warns if not marked as PI)
- Updates `Project.pi`
- Ensures new PI is a `ProjectUser` with role 'Manager' and status 'Active'
- Optionally removes the old PI's associations from the project and all its allocations

Run with:
	python scripts/change_pi.py                 # prompts for title
	python scripts/change_pi.py "My Project"    # uses provided title
"""

import os
import sys
import django
from django.db import IntegrityError


def setup_django():
	os.environ.setdefault("DJANGO_SETTINGS_MODULE", "coldfront.config.settings")
	django.setup()


def main():
	from django.contrib.auth.models import User
	from coldfront.core.project.models import (
		Project,
		ProjectUser,
		ProjectUserRoleChoice,
		ProjectUserStatusChoice,
	)
	from coldfront.core.allocation.models import (
		AllocationUser,
		AllocationUserStatusChoice,
	)

	# Get title filter from CLI args or prompt
	title_filter = None
	if len(sys.argv) > 1:
		title_filter = " ".join(sys.argv[1:]).strip()
	while not title_filter:
		title_filter = input("Enter project title (partial match allowed): ").strip()
		if not title_filter:
			print("No title provided.")

	projects = (
		Project.objects.filter(title__icontains=title_filter)
		.order_by("title")
		.select_related("pi", "status")
	)

	if not projects.exists():
		print(f"No projects found matching title '{title_filter}'.")
		return

	manager_role = ProjectUserRoleChoice.objects.get(name="Manager")
	active_status = ProjectUserStatusChoice.objects.get(name="Active")

	for project in projects:
		print("\n=== Project ===")
		print(f"Title: {project.title}")
		print(f"Current PI: {project.pi.username}")
		print(f"Status: {project.status.name}")

		# Confirm PI change for this project
		while True:
			resp = input("Change PI for this project? [y/N]: ").strip().lower()
			if resp in ("y", "yes", "n", "no", ""):
				break

		if resp not in ("y", "yes"):
			print("Skipped.")
			continue

		# Ask for the new PI username
		new_username = input("Enter new PI username: ").strip()
		if not new_username:
			print("No username provided. Skipping.")
			continue

		try:
			new_pi = User.objects.get(username=new_username)
		except User.DoesNotExist:
			print(f"User '{new_username}' does not exist. Skipping.")
			continue

		if not new_pi.is_active:
			print(f"User '{new_username}' is not active. Skipping.")
			continue

		# Warn if the user is not marked as PI (allowed, but informative)
		try:
			if hasattr(new_pi, "userprofile") and not new_pi.userprofile.is_pi:
				print(
					f"Warning: user '{new_username}' is not flagged as PI (userprofile.is_pi=False)."
				)
		except Exception:
			# If profile missing or other issues, just continue with a warning
			print(
				f"Warning: unable to verify PI flag for '{new_username}'. Proceeding anyway."
			)

		# Ensure new PI is a project member with Manager role and Active status
		pu, created = ProjectUser.objects.get_or_create(
			project=project, user=new_pi, defaults={"role": manager_role, "status": active_status}
		)
		if not created:
			# If existing, ensure they have at least Active status and Manager role
			changed = False
			if pu.status_id != active_status.id:
				pu.status = active_status
				changed = True
			if pu.role_id != manager_role.id:
				pu.role = manager_role
				changed = True
			if changed:
				pu.save()

		# Apply PI change
		old_pi_user = project.pi
		original_pi = old_pi_user.username
		project.pi = new_pi
		try:
			project.save()
		except IntegrityError as e:
			print(
				f"Failed to update project '{project.title}' PI to '{new_username}' due to integrity error: {e}. Skipping."
			)
			continue

		print(
			f"Updated PI for project '{project.title}' from '{original_pi}' to '{new_username}'."
		)

		# Ask whether to remove the old PI from the project and its allocations
		while True:
			resp_remove = input(
				"Remove old PI from project and all its allocations? [y/N]: "
			).strip().lower()
			if resp_remove in ("y", "yes", "n", "no", ""):
				break

		if resp_remove in ("y", "yes"):
			removed_proj_status = ProjectUserStatusChoice.objects.get(name="Removed")
			removed_alloc_status = AllocationUserStatusChoice.objects.get(name="Removed")

			removed_project_link = 0
			removed_allocation_links = 0

			# Project association: mark as Removed if present and not already
			try:
				pu_old = ProjectUser.objects.get(project=project, user=old_pi_user)
				if pu_old.status_id != removed_proj_status.id:
					pu_old.status = removed_proj_status
					pu_old.save()
					removed_project_link += 1
			except ProjectUser.DoesNotExist:
				pass

			# Allocation associations within this project: mark as Removed
			old_pi_alloc_users = AllocationUser.objects.filter(
				allocation__project=project, user=old_pi_user
			)
			for au in old_pi_alloc_users:
				if au.status_id != removed_alloc_status.id:
					au.status = removed_alloc_status
					au.save()
					removed_allocation_links += 1

			print(
				f"Old PI '{original_pi}' removal complete: project links removed={removed_project_link}, allocation links removed={removed_allocation_links}."
			)


if __name__ == "__main__":
	setup_django()
	try:
		main()
	except KeyboardInterrupt:
		sys.exit(1)

