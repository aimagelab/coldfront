# Generated by Django 3.2.20 on 2023-12-24 13:48

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource', '0002_auto_20191017_1141'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='historicalresource',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'historical resource', 'verbose_name_plural': 'historical resources'},
        ),
        migrations.AlterModelOptions(
            name='historicalresourceattribute',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'historical resource attribute', 'verbose_name_plural': 'historical resource attributes'},
        ),
        migrations.AlterModelOptions(
            name='historicalresourceattributetype',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'historical resource attribute type', 'verbose_name_plural': 'historical resource attribute types'},
        ),
        migrations.AlterModelOptions(
            name='historicalresourcetype',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'historical resource type', 'verbose_name_plural': 'historical resource types'},
        ),
        migrations.AlterField(
            model_name='historicalresource',
            name='history_date',
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name='historicalresourceattribute',
            name='history_date',
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name='historicalresourceattributetype',
            name='history_date',
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name='historicalresourcetype',
            name='history_date',
            field=models.DateTimeField(db_index=True),
        ),
    ]
