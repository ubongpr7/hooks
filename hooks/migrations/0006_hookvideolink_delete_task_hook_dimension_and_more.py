# Generated by Django 4.2.17 on 2025-01-22 12:27

from django.db import migrations, models
import django.db.models.deletion
import hooks.models


class Migration(migrations.Migration):

    dependencies = [
        ('hooks', '0005_remove_hook_dimension_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='HookVideoLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('video_file', models.FileField(blank=True, null=True, upload_to=hooks.models.hook_video_link)),
            ],
        ),
        migrations.DeleteModel(
            name='Task',
        ),
        migrations.AddField(
            model_name='hook',
            name='dimension',
            field=models.CharField(choices=[('option1', 'option1'), ('option2', 'option2'), ('option3', 'option3'), ('option4', 'option4')], default='option1', max_length=30),
        ),
        migrations.AddField(
            model_name='hook',
            name='parallel_processing',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='hook',
            name='status',
            field=models.CharField(default='processing', max_length=20),
        ),
        migrations.AlterField(
            model_name='hook',
            name='hooks_content',
            field=models.FileField(blank=True, max_length=500, null=True, upload_to=hooks.models.hooks_video, validators=[hooks.models.validate_video_file]),
        ),
        migrations.AddField(
            model_name='hookvideolink',
            name='hook',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='video_links', to='hooks.hook'),
        ),
    ]
