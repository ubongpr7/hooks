import os
from django.db import models
from django.core.exceptions import ValidationError

from merger.models import sanitize_filename

def validate_video_file(value):
    """
    Validate the uploaded video file to ensure it is of an allowed MIME type.
    
    Args:
        value: The file to validate.
    
    Raises:
        ValidationError: If the file's MIME type is not in the list of valid types.
    """
    # Allowed video MIME types
    valid_mime_types = [
        'video/mp4', 'video/x-m4v', 'video/quicktime', 
        'video/x-msvideo', 'video/x-ms-wmv'
    ]
    file_mime_type = value.file.content_type

    # Raise an error if the file type is not valid
    if file_mime_type not in valid_mime_types:
        raise ValidationError(
            f'Unsupported file type: {file_mime_type}. Please upload a valid video file.'
        )

def hook_video_link(instance,filename):
    return os.path.join("hook_video_links", str(instance.id), sanitize_filename(filename))

def hooks_video(instance,filename):
    return os.path.join("hooks_videos",  sanitize_filename(filename))

class Hook(models.Model):
    """
    Model representing a Hook with various attributes including video content, 
    Google Sheets link, API key, and visual properties.
    """
    user=models.ForeignKey('account.User', on_delete=models.CASCADE,null=True)

    hooks_content = models.FileField(
        max_length=500,
        upload_to=hooks_video,
        blank=True,
        null=True,
        validators=[validate_video_file]
    )
    
    progress = models.CharField(max_length=50, blank=True, null=True,default='0')
    google_sheets_link = models.URLField(max_length=500, blank=True, null=True)
    eleven_labs_api_key = models.CharField(max_length=255, blank=True, null=True)
    voice_id = models.CharField(max_length=255, blank=True, null=True)
    box_color = models.CharField(max_length=7, default='#485AFF')
    font_color = models.CharField(max_length=7, default='#FFFFFF')
    parallel_processing = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Choices for the dimension field
    STATUS_CHOICES = [
        ('option1', 'option1'), 
        ('option2', 'option2'), 
        ('option3', 'option3'),
        ('option4', 'option4')
    ]

    dimension = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default='option1'
    )
    status = models.CharField(max_length=20, default='processing')
    
    def __str__(self):
        """Return a string representation of the Hook object."""
        return str(self.id)
    def track_progress(self, increase):
        self.progress = str(increase)
        self.save()


class HookVideoLink(models.Model):
    hook=models.ForeignKey(Hook,on_delete=models.CASCADE,related_name='video_links')
    video_file=models.FileField(upload_to=hook_video_link,null=True,blank=True)

    def delete(self, *args, **kwargs):
        if self.video_file:
            self.video_file.delete()
        super().delete(*args, **kwargs)
    def Video_link_name(self):
        if self.video_file:
           return {'video_link': self.video_file.name,'file_name':self.video_file.name.split("/")[-1][:15]} 
        return
