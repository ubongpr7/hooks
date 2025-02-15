from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import re
import shutil
import subprocess
from urllib.parse import urlparse
from django.core.management.base import BaseCommand
from django.core.files.base import File        
from django.conf import settings
import requests
import boto3
from merger.models import MergeTask, VideoLinks
import logging
import os
import tempfile
from django.core.files.base import ContentFile

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)

class Command(BaseCommand):
    help = "Process video files based on MergeTask model"

    def add_arguments(self, parser):
        parser.add_argument("task_id", type=int)

    def handle(self, *args, **kwargs):
        task_id = kwargs["task_id"]
        self.merge_task= MergeTask.objects.get(id=task_id)
        merge_task=self.merge_task
        short_video_files_urls = [video.video_file.url for video in self.merge_task.short_videos.all()]
        large_video_files_urls = [video.video_file.url for video in self.merge_task.large_videos.all()]
        if not large_video_files_urls:
            logging.error("No large videos found for merging.")
            merge_task.status = 'failed'
            merge_task.save()
            return

        ref_resolution = self.check_video_format_resolution(large_video_files_urls[0])
        short_videos=self.merge_task.short_videos.all()
        large_videos=self.merge_task.large_videos.all()


        if not ref_resolution or not ref_resolution[0] or not ref_resolution[1]:
            logging.error("Invalid reference resolution. Cannot preprocess videos.")
            self.merge_task.status = 'failed'
            self.merge_task.save()
            return
        reference_resolution = ref_resolution
        per_vid=50/len(short_videos)
        # for video in large_videos:
        #     self.preprocess_video(video,reference_resolution)
        # for video in short_videos:
        #     self.preprocess_video(video,reference_resolution)
        # for video in short_videos:
        #     self.concatenate_videos(video,per_vid)
        with ThreadPoolExecutor() as executor:
            short_preprocess_futures = [
                executor.submit(self.preprocess_video, video, reference_resolution)
                for video in short_videos
            ]
            for future in as_completed(short_preprocess_futures):
                future.result()  # Wait for all short videos to finish preprocessing

    # Preprocess large videos in parallel
        with ThreadPoolExecutor() as executor:
            large_preprocess_futures = [
                executor.submit(self.preprocess_video, video, reference_resolution)
                for video in large_videos
            ]
            for future in as_completed(large_preprocess_futures):
                future.result()  # Wait for all large videos to finish preprocessing

        with ThreadPoolExecutor() as executor:
            short_concat_futures = [
                executor.submit(self.concatenate_videos, video, per_vid)
                for video in short_videos
            ]
            for future in as_completed(short_concat_futures):
                future.result() 

        with ThreadPoolExecutor() as executor:
            short_delete_futures = [
                executor.submit(self.delete_processing_files, video)
                for video in short_videos
            ]
            for future in as_completed(short_delete_futures):
                future.result() 
        with ThreadPoolExecutor() as executor:
            large_delete_futures = [
                executor.submit(self.delete_processing_files, video)
                for video in large_videos
            ]
            for future in as_completed(large_delete_futures):
                future.result() 


        self.merge_task.status='completed'
        self.merge_task.save()
        self.stdout.write(
            self.style.SUCCESS(f"Processing complete for {task_id}.")
        )
 

    def has_audio(self,video_file):
        """
        Check if the video file has an audio stream.
        """
        command = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            video_file,
        ]
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
            )
            output = result.stdout.strip()
            return output == "audio"
        except subprocess.CalledProcessError as e:
            logging.error(f"FFprobe error when checking audio for {video_file}: {e.stderr.strip()}")
            return False

    def check_video_format_resolution(self,video_file):
        """
        Uses ffprobe to retrieve the width and height of the first video stream.
        Ensures that both width and height are even numbers.
        """
        command = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            video_file
        ]
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
            )
            output = result.stdout.strip().split('\n')
            resolutions = [line.strip() for line in output if 'x' in line and line.strip()]
            if resolutions:
                try:
                    width_str, height_str = resolutions[0].split('x')[:2]
                    width = int(width_str.strip())
                    height = int(height_str.strip())
                    # Ensure dimensions are even
                    width = width if width % 2 == 0 else width + 1
                    height = height if height % 2 == 0 else height + 1
                    return width, height
                except ValueError as e:
                    logging.error(f"Error parsing resolution: {resolutions[0]} - {e}")
                    return None, None
            else:
                logging.error(f"Could not determine resolution for video: {video_file}")
                return None, None
        except subprocess.CalledProcessError as e:
            logging.error(f"FFprobe error for {video_file}: {e.stderr.strip()}")
            return None, None

    def concatenate_videos(self, video, per_vid):
            """
            Concatenates multiple video files into a single output file using FFmpeg's concat filter.
            """
            try:
                merge_task = self.merge_task

                input_files = [video.processed_file.url, merge_task.large_videos.all()[0].processed_file.url]
                final_output_name = f"{os.path.splitext(video.video_file.name.split('/')[-1])[0]}_{os.path.splitext(merge_task.large_videos.all()[0].video_file.name.split('/')[-1])[0]}.mp4"

                # final_output_name=f"{os.path.splitext(video.name.split('/')[-1])[0]}_{os.path.splitext(merge_task.large_videos.all()[0].video_file.name.split('/')[-1])[0]}"
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
                    output_file = temp_file.name
                    logging.info(f"Concatenating videos into: {output_file}")
                    if len(input_files) < 2:
                        logging.error("Need at least two files to concatenate")
                        return

                    # Build FFmpeg command with filter_complex 'concat'
                    command = ['ffmpeg', '-y']
                    for input_file in input_files:
                        command += ['-i', input_file]

                    # Construct the filter_complex string
                    filter_complex = ""
                    for i in range(len(input_files)):
                        filter_complex += f"[{i}:v][{i}:a]"
                    filter_complex += f"concat=n={len(input_files)}:v=1:a=1[outv][outa]"

                    command += [
                        '-filter_complex', filter_complex,
                        '-map', '[outv]',
                        '-map', '[outa]',
                        '-c:v', 'libx264',
                        '-preset', 'superfast',
                        '-c:a', 'aac',
                        '-pix_fmt', 'yuv420p',
                        '-r', '30',
                        output_file
                    ]

                    logging.debug(f"Concatenate command: {' '.join(command)}")
                    process = subprocess.Popen(
                        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
                    )

                    frames_processed = 0
                    prev_frames_processed = 0
                    ffmpeg_error = ""
                    while True:
                        output = process.stderr.readline()
                        if output == '' and process.poll() is not None:
                            break
                        if output:
                            logging.debug(output.strip())
                            ffmpeg_error += output
                            match = re.search(r"frame=\s*(\d+)", output)
                            if match:
                                frames_processed = int(match.group(1))
                                if frames_processed - prev_frames_processed >= 150:
                                    if merge_task:
                                        merge_task.total_frames_done += (frames_processed - prev_frames_processed)
                                        merge_task.track_progress(0)
                                        merge_task.save()
                                    prev_frames_processed = frames_processed

                    return_code = process.wait()
                    if return_code != 0:
                        logging.error(f"FFmpeg failed during concatenation of {output_file}.")
                        logging.error(f"FFmpeg error output: {ffmpeg_error}")
                        # Remove the invalid output file if FFmpeg failed
                        if os.path.exists(output_file):
                            os.remove(output_file)
                            logging.info(f"Removed invalid concatenated file: {output_file}")
                        # Retry concatenation on failure
                        raise FileNotFoundError("File not found during FFmpeg processing.")

                    if merge_task:
                        merge_task.total_frames_done += (frames_processed - prev_frames_processed)
                        merge_task.save()

                    if output_file:
                        with open(output_file, "rb") as output_video_file:
                            file_content = output_video_file.read()

                            link = VideoLinks.objects.create(
                                merge_task=merge_task
                            )
                            link.video_file.save(final_output_name, ContentFile(file_content))

                    import time
                    time.sleep(5)
                    logging.info(f"Finished concatenating: {output_file}")
                    merge_task.track_progress(per_vid)

            except FileNotFoundError as e:
                logging.warning(f"FileNotFoundError encountered: {e}. Retrying...")
                # return self.concatenate_videos(video, per_vid)

            except Exception as e:
                logging.error(f"An unexpected error occurred: {e}")
                raise

    def delete_processing_files(self, video):
        try:
            if video.processed_file:
                # Log the file being deleted
                logging.info(f"Attempting to delete processed file: {video.processed_file.name}")
                video.processed_file.delete(save=True)
                # Log success
                logging.info(f"Successfully deleted processed file: {video.processed_file.name}")
            else:
                # Log when no file exists
                logging.warning(f"No processed file found for video: {video}")
        except Exception as e:
            # Log errors
            logging.error(f"Error while deleting processed file for video: {video}. Error: {e}")

    def preprocess_video(self, video, reference_resolution=None):
        """
        Preprocesses a video by scaling it to the reference resolution and ensuring consistent encoding.
        Ensures that the output dimensions are even and that audio streams are present.
        If the input video lacks an audio stream, adds a silent audio track.
        """
        merge_task = self.merge_task
        input_file = video.video_file.url
        logging.info(f"Preprocessing video: {input_file}")

        input_has_audio = self.has_audio(input_file)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
            output_file = temp_file.name  # Temporary file for the output video

            # Build FFmpeg command
            if input_has_audio:
                # Video with audio: scale and encode
                command = ["ffmpeg", "-y", "-i", input_file]
                if reference_resolution:
                    width, height = reference_resolution
                    vf_filter = (
                        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                        f"format=yuv420p"
                    )
                    command += ["-vf", vf_filter]

                command += [
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-pix_fmt", "yuv420p",
                    "-r", "30",  # Enforce frame rate
                    output_file
                ]
            else:
                # Video without audio: add silent audio
                command = [
                    "ffmpeg", "-y", "-i", input_file,
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                ]
                if reference_resolution:
                    width, height = reference_resolution
                    vf_filter = (
                        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                        f"format=yuv420p"
                    )
                    command += ["-vf", vf_filter]

                command += [
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest",
                    "-pix_fmt", "yuv420p", "-r", "30",  # Enforce frame rate
                    output_file
                ]

            logging.debug(f"Preprocess command: {' '.join(command)}")
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
            )

            frames_processed = 0
            prev_frames_processed = 0
            while True:
                output = process.stderr.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    logging.debug(output.strip())
                    match = re.search(r"frame=\s*(\d+)", output)
                    if match:
                        frames_processed = int(match.group(1))
                        if frames_processed - prev_frames_processed >= 150:
                            if merge_task:
                                merge_task.total_frames_done += (frames_processed - prev_frames_processed)
                                merge_task.save()
                                merge_task.track_progress(0)
                            prev_frames_processed = frames_processed

            return_code = process.wait()
            if return_code != 0:
                logging.error(f"FFmpeg failed during preprocessing of {input_file}. Check logs above for details.")
                # Remove the invalid output file if FFmpeg failed
                if os.path.exists(output_file):
                    os.remove(output_file)
                    logging.info(f"Removed invalid preprocessed file: {output_file}")
                return

            if merge_task:
                merge_task.total_frames_done += (frames_processed - prev_frames_processed)
                merge_task.save()

            # Save the output file to the processed_file field of the video object
            with open(output_file, "rb") as output_video_file:
                file_content = output_video_file.read()
                video.processed_file.save(f"processed_{os.path.basename(output_file)}", ContentFile(file_content))

            # with open(output_file, 'rb') as file_data:
            #     video.processed_file.save(f"processed_{os.path.basename(output_file)}", file_data)
            
            # Cleanup temporary file
            os.remove(output_file)
            logging.info(f"Temporary file {output_file} removed after saving.")

            logging.info(f"Finished preprocessing: {video.video_file.url}")
            return video

    def create_s3_client(self):
        """    
        Creating a connection to the aws s3 bucket 
        aws_access_key_id: aws secret key 
        aws_secret_access_key: aws s3 secret key
        region_name: aws region obtained from aws bucket name
        
        """
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME
        )
        return s3_client
        
    def generate_presigned_url(self,bucket_name, object_key, expiration=3600):
        """
        Generate a presigned URL to download the S3 object.
        object_key: aws media link 
        expiration: expiration time in seconds (default: 3600 seconds)
        """
        try:
            s3_client = self.create_s3_client()
            response = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': object_key},
                ExpiresIn=expiration
            )
            print(f"Generated presigned URL: {response}")
            return response
        except Exception as e:
            print(f"Error generating presigned URL: {e}")
            return None
        

