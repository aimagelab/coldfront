# Generated by Django 2.1 on 2018-08-29 14:33

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('project', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='project',
            options={'ordering': ['title'], 'permissions': (('can_view_all_projects', 'Can see all projects'), ('can_review_pending_project_reviews', 'Can review pending project reviews'))},
        ),
    ]
