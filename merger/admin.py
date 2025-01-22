from django.contrib import admin
from .models import MergeTask,LargeVideo,ShortVideo,VideoLinks

# Register the MergeTask model with the admin site
@admin.register(MergeTask)
class TaskAdmin(admin.ModelAdmin):
    # Define the fields to be displayed in the admin list view
    list_display = [ 'status', 'user', 'total_frames',]
admin.site.register(LargeVideo)
admin.site.register(VideoLinks)
admin.site.register(ShortVideo)