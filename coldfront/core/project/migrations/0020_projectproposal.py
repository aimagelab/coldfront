# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import martor.models
import model_utils.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('field_of_science', '0001_initial'),
        ('project', '0019_projecttype_requires_funded_project_proof'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectProposal',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(
                    help_text='One-line description of the project.',
                    validators=[django.core.validators.MinLengthValidator(10, 'The description must be > 10 characters.')],
                )),
                ('description_of_research', martor.models.MartorField(
                    blank=True,
                    null=True,
                    verbose_name='Description of Research',
                    validators=[django.core.validators.MaxLengthValidator(20000)],
                )),
                ('computational_approach', martor.models.MartorField(
                    blank=True,
                    null=True,
                    verbose_name='Computational Approach',
                    validators=[django.core.validators.MaxLengthValidator(20000)],
                )),
                ('financed_project_text', models.FileField(
                    blank=True,
                    null=True,
                    upload_to='proposal_financed_project',
                    help_text='Upload the text of the funded/approved project.',
                )),
                ('funded_project_proof', models.FileField(
                    blank=True,
                    null=True,
                    upload_to='proposal_funded_proof',
                    help_text='Upload proof that the funded project has been approved (e.g. approval letter).',
                )),
                ('requested_gpu_hours', models.PositiveIntegerField(
                    help_text='Number of GPU hours requested (1 standard budget hour = 1/6 GPU hour).',
                )),
                ('requested_storage_gb', models.PositiveIntegerField(
                    help_text='Amount of WORK storage requested, in GB.',
                )),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('under_review', 'Under Review'),
                        ('approved', 'Approved'),
                        ('rejected', 'Rejected'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('applicant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='project_proposals',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('field_of_science', models.ForeignKey(
                    default=149,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='field_of_science.fieldofscience',
                )),
                ('project_type', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='proposals',
                    to='project.projecttype',
                )),
            ],
            options={
                'ordering': ['-created'],
            },
        ),
    ]
