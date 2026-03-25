# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.db import migrations, models

# (code, cleaned name, extra description)
TYPE_UPDATES = [
    ('B',  'Large research project, connected to an european or international competitive project',
           'Same size of an ISCRA AI allocation'),
    ('BI', 'Large industrial research project',
           'Same size of an ISCRA AI allocation'),
    ('C',  'Medium research project, connected to a national competitive project or a small industrial research project',
           'Same size of an ISCRA C allocation'),
    ('D',  'PhD student support project', ''),
    ('E',  'MSc thesis project', ''),
    ('F',  'Support to teaching, including group projects', ''),
    ('T',  'Blue sky project, not connected to a competitive project', ''),
]


def clean_names_and_set_descriptions(apps, schema_editor):
    ProjectType = apps.get_model('project', 'ProjectType')
    for code, name, description in TYPE_UPDATES:
        ProjectType.objects.filter(code=code).update(name=name, description=description)


def restore_names(apps, schema_editor):
    ProjectType = apps.get_model('project', 'ProjectType')
    originals = {
        'B':  'Large research project, connected to an european or international competitive project (up to 50.000 GPU hours per year, same size of an ISCRA AI allocation)',
        'BI': 'Large industrial research project (up to 50.000 GPU hours per year, same size of an ISCRA AI allocation)',
        'C':  'Medium research project, connected to a national competitive project or a small industrial research project (up to 10.000 GPU hours per year, same size of an ISCRA C allocation)',
        'D':  'PhD student support project (up to 5.000 GPU hours per year)',
        'E':  'MSc thesis project (up to 2.000 GPU hours)',
        'F':  'Support to teaching, including group projects (up to 5.000 GPU hours)',
        'T':  'Blue sky project, not connected to a competitive project (up to 5.000 GPU hours)',
    }
    for code, name in originals.items():
        ProjectType.objects.filter(code=code).update(name=name, description='')


class Migration(migrations.Migration):
    dependencies = [
        ('project', '0022_project_dates_and_durations'),
    ]

    operations = [
        migrations.AddField(
            model_name='projecttype',
            name='description',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Optional extra description shown to applicants (e.g. comparable allocation schemes).',
            ),
        ),
        migrations.RunPython(clean_names_and_set_descriptions, restore_names),
    ]
