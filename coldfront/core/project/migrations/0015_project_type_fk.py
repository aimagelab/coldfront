# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("project", "0014_projecttype"),
    ]

    operations = [
        # Add new FK field on the live project table (temporary name to avoid conflict with old CharField)
        migrations.AddField(
            model_name="project",
            name="project_type_obj",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="projects",
                to="project.projecttype",
            ),
        ),
        # simple_history stores FKs as bare integers on historical models at migration time
        migrations.AddField(
            model_name="historicalproject",
            name="project_type_obj_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]
