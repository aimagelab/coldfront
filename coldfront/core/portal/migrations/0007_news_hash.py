# Generated by Django 3.2.20 on 2024-07-28 15:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0006_documentationarticle_last_updated'),
    ]

    operations = [
        migrations.AddField(
            model_name='news',
            name='hash',
            field=models.CharField(blank=True, editable=False, max_length=100, null=True, unique=True),
        ),
    ]