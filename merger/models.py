import os
from django.db import connection, models

from utils.utils import sanitize_filename
def output_merger_video(instance,filename):
    return os.path.join("output_merger_video", str(instance.id), sanitize_filename(filename))

def short_video_path(instance, filename):
    """Generate a unique file path for each uploaded text file."""
    return os.path.join("short_videos", str(instance.merge_task.id), sanitize_filename(filename))

def large_videos(instance, filename):
    """Generate a unique file path for each uploaded text file."""
    return os.path.join("large_videos", str(instance.merge_task.id), sanitize_filename(filename))


class MergeTask(models.Model):
    user=models.ForeignKey('account.User', on_delete=models.CASCADE,null=True)
    status = models.CharField(max_length=20, default='processing')
    progress = models.CharField(max_length=20, default='0')
    total_frames_done = models.IntegerField(default=0)
    percent_done = models.IntegerField(default=0)
    
    total_frames = models.IntegerField(default=0)
    def track_progress(self, increase):
        frame_per=0
        if self.percent_done<50 and self.percent_done +increase<50:
            self.percent_done +=increase
            if self.total_frames >0:
                frame_per=(self.total_frames_done/self.total_frames)*50
                if frame_per+self.percent_done<99:
                    self.progress=str(int(frame_per+self.percent_done))
        self.save()


        

    def __str__(self) -> str:
        return self.status

class ShortVideo(models.Model):
    merge_task=models.ForeignKey(MergeTask,on_delete=models.CASCADE,related_name='short_videos')
    video_file=models.FileField(upload_to=short_video_path,null=True,blank=True)
    def delete(self, *args, **kwargs):
        if self.video_file and os.path.isfile(self.video_file.path):
            os.remove(self.video_file.path)
        super().delete(*args, **kwargs)
    def __str__(self):
        return f"{self.merge_task}-{self.merge_task.id}"
class LargeVideo(models.Model):
    merge_task=models.ForeignKey(MergeTask,on_delete=models.CASCADE,related_name='large_videos')
    video_file=models.FileField(upload_to=large_videos,null=True,blank=True)
    def delete(self, *args, **kwargs):
        if self.video_file:
            self.video_file.delete()
        super().delete(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.video_file:
            self.video_file.delete()
        super().delete(*args, **kwargs)

    def _ensure_sequence_is_aligned(self):
        """
        Ensures the database sequence is aligned with the max `id` in the table.
        """
        table_name = self._meta.db_table
        sequence_name = f"{table_name}_id_seq"
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT setval('{sequence_name}', (SELECT MAX(id) FROM {table_name}) + 1)"
            )
    def __str__(self):
        return f"{self.merge_task}-{self.merge_task.id}"
class VideoLinks(models.Model):
    merge_task=models.ForeignKey(MergeTask,on_delete=models.CASCADE,related_name='video_links')
    video_file=models.FileField(upload_to=output_merger_video,null=True,blank=True)
    def delete(self, *args, **kwargs):
        if self.video_file:
            self.video_file.delete()
        super().delete(*args, **kwargs)
    def Video_link_name(self):
        if self.video_file:
           return {'video_link': self.video_file.name,'file_name':self.video_file.name.split("/")[-1][:30]} 
        return
    def __str__(self):
        return f"{self.merge_task}-{self.merge_task.id}"