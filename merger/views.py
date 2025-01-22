# merger/views.py
import os
import zipfile
import io
import logging
import threading
from django.urls import reverse
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
import requests
from urllib.parse import urlparse
from utils.utils import  ffprobe_get_frame_count, generate_presigned_url
from .forms import VideoUploadForm
from .models import LargeVideo, MergeTask, ShortVideo
import tempfile
from django.core.management import call_command


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

@login_required
def index(request):
    """
    Renders the video upload form.
    """
    form = VideoUploadForm()
    return render(request, 'merger/index.html', {'form': form})

@require_POST
@login_required
def upload_files(request):
    """
    Handles the upload of short and large videos and saves them to S3.
    """
    max_upload_size = getattr(settings, 'FILE_UPLOAD_MAX_MEMORY_SIZE', 10485760)  # Default 10MB
    for file in request.FILES.getlist('large_videos'):
        if file.size > max_upload_size:
            messages.error(request, "One of the large videos exceeds the maximum allowed size.")
            return redirect(reverse('merger:index'))

    try:
        username = str(request.user.username).split("@")[0] if request.user.username else request.user.first_name
    except:
        username = request.user.first_name

    merge_task=MergeTask.objects.create(user=request.user)

    short_videos = request.FILES.getlist('short_videos')
    large_videos = request.FILES.getlist('large_videos')

    logging.info(f'Short videos uploaded: {short_videos}')
    logging.info(f'Large videos uploaded: {large_videos}')


    # Save and upload short videos to S3
    short_videos_models=[]
    for file in short_videos:
        short_video= ShortVideo(merge_task=merge_task,video_file=file)
        short_videos_models.append(short_video)
    ShortVideo.objects.bulk_create(short_videos_models)
    large_video=LargeVideo.objects.create(merge_task=merge_task, video_file=large_videos[0])       
    # Calculate total frames for progress tracking
    total_short_video_frames = sum(ffprobe_get_frame_count(video.video_file.url) for video in merge_task.short_videos.all())
    total_long_video_frames = sum(ffprobe_get_frame_count(video.video_file.url) for video in merge_task.large_videos.all())
    total_frames = total_short_video_frames + (len(large_videos) * total_short_video_frames) + (len(short_videos) * total_long_video_frames)
    merge_task.total_frames = total_frames if total_frames > 0 else 1
    merge_task.save()

    return JsonResponse({'taskId': merge_task.id})

@login_required
def processing(request, task_id):
    """
    Initiates the video processing in a separate thread.
    """
    try:
        merge_task = MergeTask.objects.get(id=task_id)
        merge_task.total_frames_done=0
        merge_task.save()
        if merge_task.video_links.all():
            for link in merge_task.video_links.all():
                link.delete()
    except MergeTask.DoesNotExist:
        return HttpResponse("Task not found.", status=404)

    merge_credits_used = len(merge_task.short_videos.all())
    if request.user.subscription.merge_credits < merge_credits_used:
        return HttpResponse(
            "You don't have enough merge credits, buy and try again!", status=403
        )
    try:
        username = str(request.user.username).split("@")[0] if request.user.username else request.user.first_name
    except:
        username = request.user.first_name
    def run_process_command(task_id):
            try:
                call_command("merge_videos", task_id)
            except Exception as e:
                # Handle the exception as needed (e.g., log it)
                print(f"Error processing video: {e}")


    thread = threading.Thread(target=run_process_command, args=(task_id,))
    thread.start()

    request.user.subscription.merge_credits -= merge_credits_used
    request.user.subscription.save()
    logging.info(f"Used {merge_credits_used} merge credits")

    return render(request, 'merger/processing.html', {'task_id': task_id})

@login_required
def get_progress(request, task_id):
    """
    Returns the progress of the video processing task.
    """
    merge_task = get_object_or_404(MergeTask, id=task_id)
    if merge_task.total_frames == 0:
        progress = 0
    else:
        progress = int(min(1, (merge_task.total_frames_done / merge_task.total_frames)) * 100)

    return JsonResponse({'progress': progress})


@login_required
def check_task_status(request, task_id):
    """
    Returns the status of the video processing task along with video links if completed.
    """
    task = get_object_or_404(MergeTask, id=task_id)
    videos = [video_link.video_file.url for video_link in task.video_links.all() if video_link.video_file ] or []

    return JsonResponse({
        'status': task.status,
        'video_links': videos if task.status == 'completed' else None
    })


@login_required
def processing_successful(request, task_id):
    """
    Renders a success page with links to the processed videos.
    """
    task = get_object_or_404(MergeTask, id=task_id)
    videos = [video_link.Video_link_name() for video_link in task.video_links.all()] or []

    return render(
        request, 'merger/processing_successful.html', {
            'task_id': task_id,
            'video_links': videos
        }
    )


@login_required
def download_video(request):
    """
    Downloads a video from S3 using a pre-signed URL.
    
    """
    videopath = request.GET.get('videopath', None)
    if not videopath:
        return HttpResponse("No video path provided", status=400)
    parsed_url = urlparse(videopath)
    bucket_name = parsed_url.netloc.split('.')[0]
    object_key = parsed_url.path.lstrip('/')
    presigned_url = generate_presigned_url(bucket_name, object_key)
    if not presigned_url:
        return HttpResponse("Unable to generate download link", status=500)
    try:
        response = requests.get(presigned_url, stream=True)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', 'application/octet-stream')
            response_stream = HttpResponse(response.iter_content(chunk_size=1024), content_type=content_type)
            response_stream['Content-Disposition'] = f'attachment; filename="{os.path.basename(object_key)}"'
            return response_stream
        else:
            return HttpResponse("Failed to download video from S3", status=response.status_code)
    except Exception as e:
        return HttpResponse(f"Error while downloading the file: {str(e)}", status=500)   


@login_required
def download_zip(request, task_id):
    """
    Creates and serves a ZIP archive of all processed videos for a given task.
    Downloads each video from its S3 link, stores it in a temporary file,
    and includes it in the ZIP archive.
    """
    task = get_object_or_404(MergeTask, id=task_id)
    videos = [video_link.video_file.url for video_link in task.video_links.all()] or []

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
        for video_url in videos:
            if video_url:
                try:
                    with tempfile.NamedTemporaryFile(delete=True) as temp_file:
                        response = requests.get(video_url, stream=True)
                        if response.status_code == 200:
                            temp_file.write(response.content)
                            temp_file.flush()

                            file_name = os.path.basename(video_url)
                            zip_file.write(temp_file.name, arcname=file_name)
                        else:
                            logging.warning(f"Failed to download video: {video_url}, Status code: {response.status_code}")
                except Exception as e:
                    logging.error(f"Error processing video {video_url}: {e}")

    zip_buffer.seek(0)
    
    response = HttpResponse(zip_buffer, content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="final_videos.zip"'
    return response



