# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("project", "0016_populate_projecttype"),
    ]

    operations = [
        # Drop the old CharField from both tables
        migrations.RemoveField(
            model_name="project",
            name="project_type",
        ),
        migrations.RemoveField(
            model_name="historicalproject",
            name="project_type",
        ),
        # Rename the temporary FK to the canonical name on the live table
        migrations.RenameField(
            model_name="project",
            old_name="project_type_obj",
            new_name="project_type",
        ),
        # Rename the bare integer field on the historical table
        migrations.RenameField(
            model_name="historicalproject",
            old_name="project_type_obj_id",
            new_name="project_type_id",
        ),
    ]
