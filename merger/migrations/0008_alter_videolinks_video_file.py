# Generated by Django 4.2.17 on 2025-01-22 07:13

from django.db import migrations, models
import merger.models


class Migration(migrations.Migration):

    dependencies = [
        ('merger', '0007_remove_mergetask_output_merged_video'),
    ]

    operations = [
        migrations.AlterField(
            model_name='videolinks',
            name='video_file',
            field=models.FileField(blank=True, null=True, upload_to=merger.models.output_merger_video),
        ),
    ]
