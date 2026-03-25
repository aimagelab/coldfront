# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import model_utils.fields
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('project', '0023_projecttype_description_and_clean_names'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='projectproposal',
            name='admin_notes',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Internal admin notes about the final decision (not shown to the applicant).',
            ),
        ),
        migrations.CreateModel(
            name='ProposalReview',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('review_type', models.CharField(
                    choices=[('scientific', 'Scientific'), ('technical', 'Technical')],
                    max_length=20,
                )),
                ('status', models.CharField(
                    choices=[
                        ('invited', 'Invited'),
                        ('accepted', 'Accepted'),
                        ('declined', 'Declined'),
                        ('completed', 'Completed'),
                    ],
                    default='invited',
                    max_length=20,
                )),
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('review_text', models.TextField(blank=True, default='')),
                ('score', models.PositiveSmallIntegerField(
                    blank=True,
                    null=True,
                    help_text='Score from 1 (lowest) to 5 (highest).',
                    validators=[
                        django.core.validators.MinValueValidator(1),
                        django.core.validators.MaxValueValidator(5),
                    ],
                )),
                ('proposal', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reviews',
                    to='project.projectproposal',
                )),
                ('reviewer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='proposal_reviews',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['review_type', 'created'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='proposalreview',
            unique_together={('proposal', 'reviewer', 'review_type')},
        ),
    ]
