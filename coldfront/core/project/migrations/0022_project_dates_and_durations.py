# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime
from django.db import migrations, models

# Maximum duration in months per project type code
MAX_DURATIONS = {
    'B':  48,  # 4 years
    'BI': 48,  # 4 years
    'C':  48,  # 4 years
    'D':  36,  # 3 years
    'E':  6,   # 6 months
    'F':  12,  # 1 year
    'T':  12,  # 1 year
}


def set_max_durations(apps, schema_editor):
    ProjectType = apps.get_model('project', 'ProjectType')
    for code, months in MAX_DURATIONS.items():
        ProjectType.objects.filter(code=code).update(max_duration_months=months)


class Migration(migrations.Migration):
    dependencies = [
        ('project', '0021_alter_projectproposal_computational_approach_and_more'),
    ]

    operations = [
        # Add max_duration_months to ProjectType (temporary default 12 to satisfy NOT NULL)
        migrations.AddField(
            model_name='projecttype',
            name='max_duration_months',
            field=models.PositiveIntegerField(
                default=12,
                help_text='Maximum allowed project duration in months.',
            ),
            preserve_default=False,
        ),
        migrations.RunPython(set_max_durations, migrations.RunPython.noop),

        # Add start_date / end_date to ProjectProposal
        migrations.AddField(
            model_name='projectproposal',
            name='start_date',
            field=models.DateField(
                default=datetime.date.today,
                help_text='Planned project start date.',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='projectproposal',
            name='end_date',
            field=models.DateField(
                default=datetime.date.today,
                help_text='Planned project end date.',
            ),
            preserve_default=False,
        ),
    ]
