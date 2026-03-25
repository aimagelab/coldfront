# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import django.utils.timezone
import model_utils.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("project", "0013_alter_historicalproject_description_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectType",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "created",
                    model_utils.fields.AutoCreatedField(
                        default=django.utils.timezone.now, editable=False, verbose_name="created"
                    ),
                ),
                (
                    "modified",
                    model_utils.fields.AutoLastModifiedField(
                        default=django.utils.timezone.now, editable=False, verbose_name="modified"
                    ),
                ),
                ("code", models.CharField(max_length=10, unique=True)),
                ("name", models.TextField()),
                ("annual_budget", models.IntegerField(help_text="Default annual budget in standard hours")),
                ("active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["code"],
            },
        ),
    ]
