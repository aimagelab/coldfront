# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.db import migrations

# Mirrors the old PROJECT_TYPE_CHOICES + BUDGETS_PER_PROJECT_TYPE
PROJECT_TYPES = [
    ("B",  "Large research project, connected to an european or international competitive project (up to 50.000 GPU hours per year, same size of an ISCRA AI allocation)", 300000),
    ("BI", "Large industrial research project (up to 50.000 GPU hours per year, same size of an ISCRA AI allocation)", 300000),
    ("C",  "Medium research project, connected to a national competitive project or a small industrial research project (up to 10.000 GPU hours per year, same size of an ISCRA C allocation)", 60000),
    ("T",  "Blue sky project, not connected to a competitive project (up to 5.000 GPU hours)", 30000),
    ("D",  "PhD student support project (up to 5.000 GPU hours per year)", 30000),
    ("F",  "Support to teaching, including group projects (up to 5.000 GPU hours)", 60000),
    ("E",  "MSc thesis project (up to 2.000 GPU hours)", 12000),
]


def populate_and_transfer(apps, schema_editor):
    ProjectType = apps.get_model("project", "ProjectType")
    Project = apps.get_model("project", "Project")

    # 1. Create ProjectType records
    type_map = {}
    for code, name, budget in PROJECT_TYPES:
        pt = ProjectType.objects.create(code=code, name=name, annual_budget=budget, active=True)
        type_map[code] = pt

    # 2. Transfer every existing project's old char code → new FK
    for project in Project.objects.exclude(project_type="").exclude(project_type__isnull=True):
        pt = type_map.get(project.project_type)
        if pt:
            project.project_type_obj = pt
            project.save(update_fields=["project_type_obj"])


def reverse_transfer(apps, schema_editor):
    ProjectType = apps.get_model("project", "ProjectType")
    Project = apps.get_model("project", "Project")

    # Restore old char codes from the FK before deleting ProjectType rows
    type_map = {pt.id: pt.code for pt in ProjectType.objects.all()}
    for project in Project.objects.exclude(project_type_obj__isnull=True):
        code = type_map.get(project.project_type_obj_id)
        if code:
            project.project_type = code
            project.save(update_fields=["project_type"])

    ProjectType.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("project", "0015_project_type_fk"),
    ]

    operations = [
        migrations.RunPython(populate_and_transfer, reverse_transfer),
    ]
