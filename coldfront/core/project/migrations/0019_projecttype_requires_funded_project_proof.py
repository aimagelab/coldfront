# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.db import migrations, models

# Project types that are connected to funded international/national projects
FUNDED_TYPES = {'B', 'BI', 'C'}


def set_funded_flags(apps, schema_editor):
    ProjectType = apps.get_model('project', 'ProjectType')
    ProjectType.objects.filter(code__in=FUNDED_TYPES).update(requires_funded_project_proof=True)


class Migration(migrations.Migration):
    dependencies = [
        ('project', '0018_remove_historicalproject_project_type_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='projecttype',
            name='requires_funded_project_proof',
            field=models.BooleanField(
                default=False,
                help_text='Whether applicants must upload a funded-project text and approval proof.',
            ),
        ),
        migrations.RunPython(set_funded_flags, migrations.RunPython.noop),
    ]
