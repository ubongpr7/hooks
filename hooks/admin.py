from django.contrib import admin
from .models import Hook, HookVideoLink

# Register the Hook model with the admin site
@admin.register(Hook)
class HookAdmin(admin.ModelAdmin):
    # Define the fields to display in the admin list view for Hook
    list_display = ['hooks_content', 'google_sheets_link', 'eleven_labs_api_key', 'voice_id']

# Register the Task model with the admin site
@admin.register(HookVideoLink)
class HookVideoLinkAdmin(admin.ModelAdmin):
    # Define the fields to display in the admin list view for Task
    list_display = ['hook', 'video_file']
