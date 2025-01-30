import logging
import tempfile
import os
import shutil
from venv import logger
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
import boto3

from .forms import HookForm

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from .models import Hook
from account.models import Plan
import threading
import zipfile
import io
import requests
from .tools.spreadsheet_extractor import fetch_google_sheet_data
from django.core.management import call_command
import modal
logging.basicConfig(level=logging.DEBUG)
        


@login_required
def upload_hook(request):
  """View to handle uploading a hook video."""
  
  if not request.user.is_authenticated:
    return redirect('account:home')

  hook = None
  if request.method == 'POST':
    # task_id = generate_task_id()
    try:
        username = str(request.user.username).split("@")[0] if request.user.username else request.user.first_name
    except:
        username = request.user.first_name

    parallel_processing = True

    form = HookForm(request.POST, request.FILES)
    is_valid_resolution = request.POST.get('resolution') in [
      'option1', 'option2', 'option3', 'option4'
    ]
    is_valid_form = form.is_valid() and \
                    is_valid_resolution
    if is_valid_form:
      hook = form.save(commit=False)
      # hook.task_id = task_id
      hook.parallel_processing = parallel_processing
      hook.dimension = request.POST.get('resolution')
      hook.user=request.user
      hook.save()

      return redirect(
        'hooks:processing', task_id=hook.id, aspect_ratio=hook.dimension
      )
    else:
      return render(request, 'upload_hook.html', {'form': form, 'hook': hook})
  else:
    form = HookForm()

  return render(request, 'upload_hook.html', {'form': form, 'hook': hook})


# @login_required
# def processing(request, task_id, aspect_ratio):
#   def run_process_command():
#     try:
#         call_command("process_hook", task_id)
#     except Exception as e:
#         print(f"Error processing video: {e}")

#   # Check if the user has enough credits
#   user_sub = request.user.subscription
#   if not user_sub or user_sub.hooks <= 0:
#     # You can change the url below to the stripe URL
#     # return redirect('hooks:no_credits')  # Redirect to an error page or appropriate view
#     return HttpResponse(
#       "You don't have enough credits, buy and try again!", status=404
#     )
  

#   try:
#       username = str(request.user.username).split("@")[0] if request.user.username else request.user.first_name
#   except:
#       username = request.user.first_name


#   thread = threading.Thread(target=run_process_command)
#   thread.start()


#   return render(
#     request, 'processing.html', {
#       'task_id': task_id,
#       'aspect_ratio': aspect_ratio,
#     }
#   )

@login_required
def processing(request, task_id, aspect_ratio):
    # Check credits first
    user_sub = request.user.subscription
    if not user_sub or user_sub.hooks <= 0:
        return HttpResponse("You don't have enough credits, buy and try again!", status=404)

    try:
        # Get the Modal function reference
        process_hook = modal.Function.lookup("django-hook-processor", "process_hook")
        
        # Start the Modal job asynchronously
        modal_call = process_hook.spawn(task_id)
        
        # Store Modal call ID with your task (add field to Hook model)
        hook = Hook.objects.get(id=task_id)
        hook.modal_call_id = modal_call.object_id
        hook.save()

    except Exception as e:
        logger.error(f"Failed to start Modal job: {e}")
        return HttpResponse("Processing failed to start", status=500)

    return render(
        request, 
        'processing.html', 
        {
            'task_id': task_id,
            'aspect_ratio': aspect_ratio,
            'modal_call_id': modal_call.object_id  # Pass to frontend for status checks
        }
    )

@login_required
def check_task_status(request, task_id):
  task = get_object_or_404(Hook, id=task_id)
  videos=[video.video_file.url for video in task.video_links.all() if video.video_file ]
  # Return task status and video links (if processing is completed)
  return JsonResponse(
    {
      'status': task.status,
      'video_links': videos if task.status == 'completed' else None,
      "progress":task.progress
    }
  )
  
  
  


def processing_successful(request, task_id):
  """View to display processing successful page."""
  
  task = get_object_or_404(Hook, id=task_id)
  videos=[video.Video_link_name() for video in task.video_links.all() if video.video_file ]

  return render(
    request, 'processing_successful.html', {
      'task_id': task_id,
      'video_links': videos,
      'plans': Plan.objects.all(),
    }
  )
  




@login_required
def download_video(request):
    file_key = request.GET.get('videopath', None)
    
    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


    try:
        # Get the file from S3
        s3_response = s3.get_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=file_key
        )
        print('===========>',file_key)
        # Set the appropriate headers for file download
        response = HttpResponse(
            s3_response["Body"].read(), content_type=s3_response["ContentType"]
        )
      
        response["Content-Disposition"] = (
              f'attachment; filename="{file_key.split("/")[-1]}"'
          )
        response["Content-Length"] = s3_response["ContentLength"]

        return response
    except s3.exceptions.NoSuchKey:
        return HttpResponse("File not found.", status=404)
    except :
        return HttpResponse("Credentials not available.", status=403)




def download_zip(request, task_id):
  task = get_object_or_404(Hook, id=task_id)
  videos=[video.video_file.url for video in task.video_links.all() if video.video_file ]

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
  response['Content-Disposition'] = f'attachment; filename="hook_videos.zip"'

  return response



@login_required
def validate_google_sheet_link(request):
  """
    View to validate Google Sheets link.
  """
  if request.method == 'POST':
    google_sheets_link = request.POST.get('google_sheets_link')

    try:
      # Attempt to fetch the Google Sheets data for validation
      fetch_google_sheet_data(google_sheets_link)
      return JsonResponse({'valid': True})
    except ValueError as ve:
      return JsonResponse({'valid': False, 'error': str(ve)})
    except Exception as e:
      return JsonResponse({'valid': False, 'error': str(e)})

  return JsonResponse({'valid': False, 'error': 'Invalid request method.'})




def validate_api_key(request):
    """
    View to validate Eleven Labs API key.
    """
    if request.method == 'POST':
        api_key = request.POST.get('eleven_labs_api_key', '')
        voice_id = request.POST.get('voice_id')

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {"xi-api-key": api_key}
        data = {
            "text": "Test voice synthesis",
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
        }

        try:
            response = requests.post(url, json=data, headers=headers)
            if response.status_code == 200:
                return JsonResponse({'valid': True})
            else:
                error_detail = response.json().get('detail', {})
                return JsonResponse({'valid': False, 'error': error_detail.get('status'), 'message': error_detail.get('message')})
        except requests.exceptions.RequestException:
            return JsonResponse({'valid': False, 'error': 'Error Connecting To Eleven Labs API', 'message': 'Error Connecting To Eleven Labs API'})
