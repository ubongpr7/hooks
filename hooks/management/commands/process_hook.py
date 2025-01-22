import logging
import os
import shutil
import tempfile
from django.core.management.base import BaseCommand
from django.core.files.base import File        
import logging
import os
import threading

from hooks.models import Hook, HookVideoLink

from tqdm import tqdm
import pandas as pd
from moviepy.editor import AudioFileClip

from django.core.cache import cache
from django.http import JsonResponse

from hooks.tools.utils import hex_to_rgb, handle_task_cancellation, delete_temp_dir
from hooks.tools.spreadsheet_extractor import fetch_google_sheet_data, extract_word_color_data
from hooks.tools.audio_processors import process_audios
from hooks.tools.video_processors import process_audio_on_videos


logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)




canceled_tasks = set()


class Command(BaseCommand):
    help = "Process video files based on TextFile model"

    def add_arguments(self, parser):
        parser.add_argument("task_id", type=int)

    def handle(self, *args, **kwargs):
        task_id = kwargs["task_id"]
        self.hook= Hook.objects.get(id=task_id)
        self.update_progress(1)
        processing=self.background_processing()
        self.stdout.write(
            self.style.SUCCESS(f"Processing complete for {task_id}.")
        )
    def update_progress(self,increase):
        self.hook.track_progress(increase)
    def process(self, params):
        task_id = params.get('task_id', self.hook.id)

        try:
            input_df = params['input_df']
            if 'Hook Text' not in input_df.columns:
                raise Exception("The column 'Hook Text' does not exist in the DataFrame.")

            google_sheet_link = params.get('google_sheet_link')
            if not google_sheet_link:
                raise Exception("Missing 'google_sheet_link' in params.")

            word_color_data = extract_word_color_data(google_sheet_link)
            per_video=int(45/len(input_df))

            for idx, row in input_df.iterrows():
                hook_text = row['Hook Text']

            ELEVENLABS_API_KEY = params['api_key']
            no_of_parallel_executions = params['parallel_processing']

            INPUT_DIR = params['input_dir']
            OUTPUT_DIR = params['output_dir']
            voice_id = params['voice_id']
            temp_dir = params['temp_dir']
            top_box_color = params['top_box_color']
            default_text_color = params['default_text_color']

            input_videos_folder = os.path.join(INPUT_DIR, 'video')
            output_audios_folder = os.path.join(OUTPUT_DIR, 'audios')
            output_videos_folder = os.path.join(OUTPUT_DIR, 'videos')
            is_tiktok = False
            if params['aspect_ratio'] == 'option1':
             OUT_VIDEO_DIM = "1080x1080"
            elif params['aspect_ratio'] == 'option2':
                OUT_VIDEO_DIM = "1080x1350"
            elif params['aspect_ratio'] == 'option3':
                OUT_VIDEO_DIM = "1080x1920"
                is_tiktok = True
            elif params['aspect_ratio'] == 'option4':
                OUT_VIDEO_DIM = "1920x1080"
            else:
                raise ValueError(f"Unsupported aspect ratio: {params['aspect_ratio']}")

            OUT_VIDEO_HEIGHT = int(OUT_VIDEO_DIM.split('x')[1])
            OUT_VIDEO_WIDTH = int(OUT_VIDEO_DIM.split('x')[0])

            if len(os.listdir(input_videos_folder)) == 0:
                raise Exception(
                    f"input/videos folder {input_videos_folder} does not contain any videos"
                )
            video_files = sorted(
                [
                    f for f in os.listdir(input_videos_folder)
                    if f.endswith('.mp4') or f.endswith('.mov')
                ]
            )

            for col in ["Hook Video Filename", "Input Video Filename", "Audio Filename",
                        "Voice"]:
                if col not in input_df.columns:
                    input_df[col] = ''

            l_unprocessed_rows = len(input_df[input_df['Hook Video Filename'] == ''])

            all_hooks = []
            total_rows = len(input_df)
            current_row = 0

            for idx_1, row in tqdm(input_df.iterrows(), total=total_rows,desc="Processing rows"):
                hook_text = row['Hook Text']
                hook_number = idx_1 + 1

                process_audios(
                    ELEVENLABS_API_KEY, row, hook_number, hook_text, input_df, idx_1,
                    output_audios_folder, voice_id
                )
                
                logging.info('Audio proccessed successfully')

            current_thread_count = 0

            self.update_progress(20)
            for idx, row in tqdm(input_df.iterrows(), total=total_rows,desc="Processing rows"):
                hook_text = row['Hook Text']
                hook_number = idx + 1

                audio_clip = AudioFileClip(
                    os.path.join(output_audios_folder, row['Audio Filename'])
                )
                video_index = idx % len(video_files)
                num_videos_to_use = int(round(audio_clip.duration / 2))

                video_file_size = len(video_files)
                if num_videos_to_use + video_index > video_file_size:
                    num_videos_to_use = video_file_size - video_index

                last_video = video_index + num_videos_to_use
                video_files_to_use = [
                    os.path.join(input_videos_folder, video_files[i])
                    for i in range(video_index, last_video)
                ]

                if params['task_id'] in canceled_tasks:
                    return handle_task_cancellation(temp_dir, task_id)

                hook_job = threading.Thread(
                    target=process_audio_on_videos,
                    args=(
                    row, video_files_to_use, idx, input_df, hook_number, hook_text,
                    num_videos_to_use, audio_clip, OUT_VIDEO_WIDTH, OUT_VIDEO_HEIGHT,
                    output_videos_folder, total_rows, task_id, top_box_color,
                    default_text_color, word_color_data, None, params['add_watermark'],
                    is_tiktok
                    )
                )
                hook_job.start()
                all_hooks.append(hook_job)
                current_thread_count += 1
                if current_thread_count == int(no_of_parallel_executions):
                    for hook in all_hooks:
                        hook.join()
                    all_hooks.clear()
                    current_thread_count = 0
            total_tasks = len(all_hooks)
            for hook in all_hooks:
                try:
                    hook.join()
                except Exception as err:
                    logging.error(f'failed to join all hooks --> {str(err)}')

            # Now generate the video links after all processing is complete
            credits_used = 0
            video_links = []
            for idx, row in input_df.iterrows():
                logging.info('Trying to generate link')
                row['Hook Video Filename'] = f'hook_{idx}.mp4'
                video_path = os.path.join(
                    output_videos_folder, row['Hook Video Filename']
                )
                video_links.append(
                    {
                    'file_name': row['Hook Video Filename'],
                    'video_link': video_path
                    }
                )
                credits_used += 1
                logging.info("used one credit")
                logging.info(
                    f"Generated video link with file name: {row['Hook Video Filename']}"
                )

            logging.info(f"Task {task_id} completed.")
            return video_links, credits_used

        except Exception as e:
            logging.error(f"Error during processing ---> {str(e)}")
            delete_temp_dir(params.get('temp_dir', ''))

    def process_files(
        self, temp_dir, add_watermark=False, aspect_ratio='option1'
        ):

        hook_object = self.hook
        if not hook_object:
            return JsonResponse({"error": "Invalid Task id"})

        video_file= hook_object.hooks_content
        google_sheet_link = hook_object.google_sheets_link
        voice_id = hook_object.voice_id
        api_key = hook_object.eleven_labs_api_key
        parallel_processing = hook_object.parallel_processing
        top_box_color_value = hook_object.box_color
        main_box_color_value = hook_object.font_color

        top_box_color = hex_to_rgb(top_box_color_value)
        default_text_color = hex_to_rgb(main_box_color_value)
        self.update_progress(5)


        if not video_file or not google_sheet_link or not voice_id or not api_key or not parallel_processing:
            return JsonResponse({"error": "Missing form data"})

        input_videos_folder = os.path.join(temp_dir, 'input', 'video')
        output_audios_folder = os.path.join(temp_dir, 'output', 'audios')
        output_videos_folder = os.path.join(temp_dir, 'output', 'videos')

        # Ensure the directories exist
        os.makedirs(input_videos_folder, exist_ok=True)
        os.makedirs(output_audios_folder, exist_ok=True)
        os.makedirs(output_videos_folder, exist_ok=True)

        # Save the video file
        video_files_paths = []
        video_file_name = os.path.basename(video_file.name)
        video_file_path = os.path.join(input_videos_folder, video_file_name)
        os.makedirs(os.path.dirname(video_file_path), exist_ok=True)
        with open(video_file_path, 'wb+') as destination:
            for chunk in video_file.chunks():
                destination.write(chunk)
        video_files_paths.append(video_file_path)

        google_sheet_data = fetch_google_sheet_data(google_sheet_link)
        extract_word_color_data(google_sheet_link)
        input_df = pd.DataFrame(google_sheet_data)
        self.update_progress(15)

        if input_df.empty or ('Hook Text' not in input_df.columns and input_df.shape[1] > 0):
            if input_df.shape[1] == 1:
                input_df.columns = ['Hook Text']
            else:
                return JsonResponse(
                    {
                    "error":
                        "Ensure the google sheet access is updated to anyone with link."
                    }
                )

        # Create a params dictionary to pass to the background task
        params = {
            "input_dir": os.path.join(temp_dir, 'input'),
            "output_dir": os.path.join(temp_dir, 'output'),
            "video_files_paths": video_files_paths,
            "voice_id": voice_id,
            "api_key": api_key,
            "parallel_processing": parallel_processing,
            "task_id": hook_object.id,
            "temp_dir": temp_dir,
            "top_box_color": top_box_color,
            "default_text_color": default_text_color,
            "input_df": input_df,
            "google_sheet_link": google_sheet_link,
            "add_watermark": add_watermark,
            "aspect_ratio": aspect_ratio,
        }
        cache.set(f"{hook_object.id}", temp_dir, timeout=600)

        video_links, credits_used = self.process(params)
        return video_links, credits_used
   
    def background_processing(self):
        """Background processing for the given task."""
        hook=self.hook
        task_id=hook.id
        user_sub=hook.user.subscription
        aspect_ratio=hook.dimension

        try:
            temp_dir = tempfile.mkdtemp(prefix=f"{task_id}")
            logging.info(f"Temporary directory created: {temp_dir}")
            video_links, credits_used = self.process_files(
                temp_dir,
                user_sub.plan.name.lower() == 'free',
                aspect_ratio
            )
            logging.info(f"Video Links: {video_links}")
            logging.info(f"Credits Used: {credits_used}")
            user_sub.hooks -= credits_used
            user_sub.save()
            logging.info(f"User credits reduced by {credits_used}. New credit balance: {user_sub.hooks}")
            updated_video_links = []
            percent_per_video=int(20/len(video_links))

            for video in video_links:

                video_file_path = video.get('video_link') 
                video_file_name = video.get('file_name')
                if video_file_path:
                    with open(video_file_path, 'rb') as f:
                        file_content = File(f)
                        hook_video_link =HookVideoLink.objects.create(hook=hook)
                        hook_video_link.video_file.save(video_file_name,file_content)
                percent_per_video+=percent_per_video

            
            self.update_progress(100)
            hook.status = 'completed'
            hook.save()
            logging.info(f"Task {task_id} updated to 'completed' with video URLs.")


        except Exception as e:
            logging.error(f"Error during background processing: {e}")

        finally:
            try:
                if 'temp_dir' in locals():
                    shutil.rmtree(temp_dir)
                    logging.info(f"Temporary directory {temp_dir} deleted.")
            except Exception as cleanup_error:
                logging.error(f"Error during temporary directory cleanup: {cleanup_error}")
