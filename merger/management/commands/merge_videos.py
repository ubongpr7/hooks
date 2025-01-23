from concurrent.futures import ThreadPoolExecutor
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

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


class Command(BaseCommand):
    help = "Process video files based on TextFile model"

    def add_arguments(self, parser):
        parser.add_argument("task_id", type=int)

    def handle(self, *args, **kwargs):
        task_id = kwargs["task_id"]
        self.merge_task= MergeTask.objects.get(id=task_id)
        processing=self.process_videos()
        self.stdout.write(
            self.style.SUCCESS(f"Processing complete for {task_id}.")
        )

    def sanitize_filename(self,filename):
        """
        Removes or replaces characters that are unsafe for filenames.
        """
        # Replace spaces with underscores
        filename = filename.replace(' ', '_')
        # Remove any character that is not alphanumeric, underscore, hyphen, or dot
        filename = re.sub(r'[^\w\-_\.]', '', filename)
        return filename

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


    def ffprobe_get_frame_count(self,video_filepath):
        """
        Uses ffprobe to count the number of video frames in a video file.
        """
        command = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-count_frames',
            '-show_entries', 'stream=nb_read_frames',
            '-of', 'csv=p=0',
            video_filepath,
        ]
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
            )
            output = result.stdout.strip()
            if output.isdigit():
                return int(output)
            else:
                logging.error(f"Couldn't get frame count for {video_filepath}: {result.stderr.strip()}")
                return 0
        except subprocess.CalledProcessError as e:
            logging.error(f"FFprobe error for {video_filepath}: {e.stderr.strip()}")
            return 0
        

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

    def concatenate_videos(self,input_files, output_file, merge_task):
        """
        Concatenates multiple video files into a single output file using FFmpeg's concat filter.
        """
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
            return

        if merge_task:
            merge_task.total_frames_done += (frames_processed - prev_frames_processed)
            merge_task.save()

        logging.info(f"Finished concatenating: {output_file}")
    
    def preprocess_video(self,input_file, output_file, reference_resolution=None, merge_task=None):
        """
        Preprocesses a video by scaling it to the reference resolution and ensuring consistent encoding.
        Ensures that the output dimensions are even and that audio streams are present.
        If the input video lacks an audio stream, adds a silent audio track.
        """
        logging.info(f"Preprocessing video: {input_file}")

        # Check if the input video has an audio stream
        input_has_audio = self.has_audio(input_file)

        if input_has_audio:
            # Video with audio: scale and encode
            command = ["ffmpeg", "-y", "-i", input_file]

            if reference_resolution:
                width, height = reference_resolution
                # Scale with aspect ratio preservation and enforce even dimensions
                vf_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                    f"format=yuv420p"
                )
                command += ["-vf", vf_filter]

            # Ensure audio is encoded
            command += [
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-c:a", "aac",
                "-pix_fmt", "yuv420p",
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

            # Map video and silent audio
            command += [
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-c:a", "aac",
                "-shortest",
                "-pix_fmt", "yuv420p",
                "-r", "30",  # Enforce frame rate
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

        logging.info(f"Finished preprocessing: {output_file}")
        

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
        

    def download_from_s3(self,file_key, local_file_path):

        """
        Download a file from S3 and save it to a local path.

        Args:
            file_key (str): The S3 object key (file path in the bucket).
            local_file_path (str): The local file path where the file will be saved.

        Returns:
            bool: True if successful, False otherwise.
            
        """
        AWS_ACCESS_KEY_ID = settings.AWS_ACCESS_KEY_ID
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        aws_secret = settings.AWS_SECRET_ACCESS_KEY
        s3 = boto3.client(
            "s3", aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=aws_secret
        )

        try:
            # Download the file from the bucket using its S3 object key
            response = s3.get_object(Bucket=bucket_name, Key=file_key)
            object_content = response["Body"].read()
            logging.info(f"Downloaded {file_key} from S3 to {local_file_path}")
            return object_content
        except Exception as e:
            logging.error(f"Failed to download {file_key} from S3: {e}")
            return False
        

    def download_video_from_s3(self,s3_url, local_folder):
        """
        Download an S3 file to a local folder using a presigned URL.
        s3_url: The presigned URL to download the file from S3
        local_folder: The local folder to save the downloaded file
        """
        # Parse the S3 URL
        parsed_url = urlparse(s3_url)
        bucket_name = parsed_url.netloc.split('.')[0]  # Extract the bucket name
        bucket_name = bucket_name if bucket_name else settings.AWS_STORAGE_BUCKET_NAME
        object_key = parsed_url.path.lstrip('/')       # Extract the object key

        # Generate a presigned URL
        presigned_url = self.generate_presigned_url(bucket_name, object_key)
        if not presigned_url:
            print("Failed to generate a presigned URL")
            return

        # Fetch the file and save it locally
        local_file_path = os.path.join(local_folder, os.path.basename(object_key))
        try:
            response = requests.get(presigned_url, stream=True)
            if response.status_code == 200:
                with open(local_file_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=1024):
                        file.write(chunk)
                print(f"File downloaded successfully: {local_file_path}")
            else:
                print(f"Failed to download file. Status code: {response.status_code}")
        except Exception as e:
            print(f"Error downloading the file: {e}")



    def get_file_names(self,s3_urls):
        """
        Extracts meaningful names from a list of S3 keys or URLs.

        Args:
            s3_urls (list): List of S3 URLs or keys.

        Returns:
            list: List of extracted names without extensions.
        """
        names = []
        for s3_url in s3_urls:
            parsed_url = urlparse(s3_url)
            filename = os.path.basename(parsed_url.path)  
            names.append(os.path.splitext(filename)[0])  
        return names


    def process_videos(self):
        """
        Orchestrates the preprocessing and concatenation of videos for a given task.
        """
        logging.info("Starting video processing...")
        merge_task = self.merge_task

        short_video_files = [video.video_file.url for video in merge_task.short_videos.all()]
        large_video_files = [video.video_file.url for video in merge_task.large_videos.all()]

        if not large_video_files:
            logging.error("No large videos found for merging.")
            merge_task.status = 'failed'
            merge_task.save()
            return

        ref_resolution = self.check_video_format_resolution(large_video_files[0])
        if not ref_resolution or not ref_resolution[0] or not ref_resolution[1]:
            logging.error("Invalid reference resolution. Cannot preprocess videos.")
            merge_task.status = 'failed'
            merge_task.save()
            return

        reference_resolution = ref_resolution
        logging.info(f"Reference resolution: {reference_resolution}")

        try:
            # Use a temporary directory for output files
            with tempfile.TemporaryDirectory() as temp_dir:
                preprocessed_short_files = []
                short_video_names = self.get_file_names(short_video_files)
                futures = []

                # Preprocess short videos
                with ThreadPoolExecutor() as executor:
                    for video in short_video_files:
                        # short_name = os.path.splitext(os.path.basename(video))[0]
                        # short_video_names.append(short_name)

                        temp_file = tempfile.NamedTemporaryFile(dir=temp_dir, delete=False, suffix=".mp4")
                        temp_file.close()
                        output_file = temp_file.name

                        self.download_video_from_s3(video, output_file)
                        futures.append(executor.submit(self.preprocess_video, video, output_file, reference_resolution, merge_task))
                        preprocessed_short_files.append(output_file)

                    for future in futures:
                        try:
                            future.result()
                        except Exception as e:
                            logging.error(f"Error during preprocessing: {e}")
                            merge_task.status = 'failed'
                            merge_task.save()
                            return

                # Preprocess large videos
                preprocessed_large_files = []
                large_video_names = self.get_file_names(large_video_files)
                futures = []

                with ThreadPoolExecutor() as executor:
                    for video in large_video_files:
                        # large_name = os.path.splitext(os.path.basename(video))[0]
                        # large_video_names.append(large_name)

                        temp_file = tempfile.NamedTemporaryFile(dir=temp_dir, delete=False, suffix=".mp4")
                        temp_file.close()
                        output_file = temp_file.name

                        self.download_video_from_s3(video, output_file)
                        futures.append(executor.submit(self.preprocess_video, video, output_file, reference_resolution, merge_task))
                        preprocessed_large_files.append(output_file)

                    for future in futures:
                        try:
                            future.result()
                        except FileNotFoundError as e:
                            logging.error(f"Error during preprocessing: {e}")
                            merge_task.track_progress(0)
                            return self.preprocess_video()
                        except Exception as e:
                            logging.error(f"Error during preprocessing: {e}")
                            merge_task.status = 'failed'
                            merge_task.save()
                            return

                final_output_files = []
                with ThreadPoolExecutor() as executor:
                    concat_futures = []
                    for large_video, large_name in zip(preprocessed_large_files, large_video_names):
                        for short_file, sname in zip(preprocessed_short_files, short_video_names):
                            short_base = os.path.splitext(os.path.basename(short_file))[0].replace('preprocessed_', '')
                            large_base = os.path.splitext(os.path.basename(large_video))[0].replace('preprocessed_', '')
                            final_output_name = f"{short_base}_{large_base}.mp4"

                            temp_file = tempfile.NamedTemporaryFile(dir=temp_dir, delete=False, suffix=".mp4")
                            temp_file.close()
                            final_output = temp_file.name

                            concat_futures.append(
                                executor.submit(self.concatenate_videos, [short_file, large_video], final_output, merge_task)
                            )
                            final_output_files.append({
                                'video_link': final_output,
                                'file_name': final_output_name
                            })

                    for future in concat_futures:
                        try:
                            future.result()
                        except Exception as e:
                            logging.error(f"Error during concatenation: {e}")
                            merge_task.status = 'failed'
                            merge_task.save()
                            return

                # Save video links to the database
                merge_task.track_progress(50)
                per_vid=int(50/len(final_output_files))
                for video in final_output_files:
                    video_file_path = video.get('video_link')
                    video_file_name = video.get('file_name')
                    if video_file_path:
                        with open(video_file_path, 'rb') as f:
                            file_content = File(f)
                            link = VideoLinks.objects.create(
                                merge_task=merge_task
                            )
                            link.video_file.save(video_file_name, file_content)
                            merge_task.track_progress(int(int(merge_task.progress)+ per_vid))
            logging.info("Video processing complete!")
            merge_task.status = 'completed'
            merge_task.save()

        except Exception as e:
            logging.error(f"An error occurred during video processing: {e}")
            merge_task.status = 'failed'
            merge_task.save()
        

    # def process_videos(self):
    #     """
    #     Orchestrates the preprocessing and concatenation of videos for a given task.
        
    #     """
    #     logging.info("Starting video processing...")

    #     merge_task = self.merge_task

    #     short_video_files = [video.video_file.url for video in merge_task.short_videos.all()]
    #     large_video_files = [video.video_file.url for video in merge_task.large_videos.all()]


    #     if not large_video_files:
    #         logging.error("No large videos found for merging.")
    #         merge_task.status = 'failed'
    #         merge_task.save()
    #         return

    #     ref_resolution = self.check_video_format_resolution(large_video_files[0])
    #     if not ref_resolution or not ref_resolution[0] or not ref_resolution[1]:
    #         logging.error("Invalid reference resolution. Cannot preprocess videos.")
    #         merge_task.status = 'failed'
    #         merge_task.save()
    #         return

    #     reference_resolution = ref_resolution
    #     logging.info(f"Reference resolution: {reference_resolution}")

    #     preprocessed_short_files = []
    #     short_video_names = []
    #     with ThreadPoolExecutor() as executor:
    #         futures = []
    #         for video in short_video_files:
    #             short_name = os.path.splitext(os.path.basename(video))[0]
    #             short_video_names.append(short_name)
    #             preprocessed_filename = f"preprocessed_{os.path.basename(video)}"
    #             output_file = os.path.join(settings.OUTPUT_FOLDER, preprocessed_filename)
    #             self.download_video_from_s3(video, output_file) 
    #             futures.append(executor.submit(self.preprocess_video, video, output_file, reference_resolution, merge_task))
    #             preprocessed_short_files.append(output_file)

    #         for future in futures:
    #             try:
    #                 future.result()
    #             except Exception as e:
    #                 logging.error(f"Error during preprocessing: {e}")
    #                 merge_task.status = 'failed'
    #                 merge_task.save()
    #                 return

    #     preprocessed_large_files = []
    #     large_video_names = []
    #     with ThreadPoolExecutor() as executor:
    #         futures = []
    #         for video in large_video_files:

    #             large_name = os.path.splitext(os.path.basename(video))[0]
    #             large_video_names.append(large_name)
    #             preprocessed_filename = f"preprocessed_{os.path.basename(video)}"
    #             output_file = os.path.join(settings.OUTPUT_FOLDER, preprocessed_filename)
    #             self.download_video_from_s3(video, output_file)
    #             futures.append(executor.submit(self.preprocess_video, video, output_file, reference_resolution, merge_task))
    #             preprocessed_large_files.append(output_file)

    #         for future in futures:
    #             try:
    #                 future.result()
    #             except Exception as e:
    #                 logging.error(f"Error during preprocessing: {e}")
    #                 merge_task.status = 'failed'
    #                 merge_task.save()
    #                 return

    #     valid_preprocessed_short_files = []
    #     valid_short_names = []
    #     for pre_file, sname in zip(preprocessed_short_files, short_video_names):
    #         w, h = self.check_video_format_resolution(pre_file)
    #         if w and h:
    #             valid_preprocessed_short_files.append(pre_file)
    #             valid_short_names.append(sname)
    #         else:
    #             logging.error(f"Preprocessed file {pre_file} does not contain a valid video stream.")

    #     if not valid_preprocessed_short_files:
    #         logging.error("No valid preprocessed short videos available for concatenation.")
    #         merge_task.status = 'failed'
    #         merge_task.save()
    #         return
        

    #     valid_preprocessed_large_files = []
    #     valid_large_names = []
    #     for pre_file, lname in zip(preprocessed_large_files, large_video_names):
    #         w, h = self.check_video_format_resolution(pre_file)
    #         if w and h:
    #             valid_preprocessed_large_files.append(pre_file)
    #             valid_large_names.append(lname)
    #         else:
    #             logging.error(f"Preprocessed file {pre_file} does not contain a valid video stream.")

    #     if not valid_preprocessed_large_files:
    #         logging.error("No valid preprocessed large videos available for concatenation.")
    #         merge_task.status = 'failed'
    #         merge_task.save()
    #         return

    #     final_output_files = []
    #     with ThreadPoolExecutor() as executor:
    #         concat_futures = []
    #         for large_video, large_name in zip(valid_preprocessed_large_files, valid_large_names):
    #             # Concatenate each short video with the large video
    #             for short_file, sname in zip(valid_preprocessed_short_files, valid_short_names):
    #                 # Remove 'preprocessed_' prefix for naming
    #                 short_base = os.path.splitext(os.path.basename(short_file))[0].replace('preprocessed_', '')
    #                 large_base = os.path.splitext(os.path.basename(large_video))[0].replace('preprocessed_', '')
    #                 final_output_name = f"{short_base}_{large_base}.mp4"
    #                 final_output = os.path.join(settings.OUTPUT_FOLDER, final_output_name)
    #                 concat_futures.append(
    #                     executor.submit(self.concatenate_videos, [short_file, large_video], final_output, merge_task)
    #                 )

    #                 relative_output = os.path.relpath(final_output, settings.MEDIA_ROOT)
    #                 final_output_files.append({
    #                     'video_link': relative_output.replace('\\', '/'),  # Ensure URL-friendly paths
    #                     'file_name': final_output_name
    #                 })

    #         for future in concat_futures:
    #             try:
    #                 future.result()
    #             except Exception as e:
    #                 logging.error(f"Error during concatenation: {e}")
    #                 merge_task.status = 'failed'
    #                 merge_task.save()
    #                 return
    #     updated_video_links = []
    #     for video in final_output_files:
    #             video_file_path = video.get('video_link')
    #             video_file_name = video.get('file_name')
    #             if video_file_path:
    #                 with open(video_file_path, 'rb') as f:
    #                     file_content = File(f)
    #                     link=VideoLinks.objects.create(
    #                         merge_task=merge_task
    #                     )
                        
    #                     link.video_file.save(video_file_name, file_content)                    # s3_key = f"output_merger_videos/{username}/{task_id}/{video_file_name}"
    #                 # video_url = upload_to_s3(video_file_path, settings.AWS_STORAGE_BUCKET_NAME, s3_key)
    #                 # updated_video_links.append({
    #                 #     "file_name": video_file_name,
    #                 #     "video_link": video_url
    #                 # })

    #     logging.info("Video processing complete!")
    #     merge_task.status = 'completed'
    #     # merge_task.video_links = updated_video_links
    #     merge_task.save()
        
    #     try:
    #         # Delete temporary files
    #         if os.path.exists(settings.OUTPUT_FOLDER):
    #             shutil.rmtree(settings.OUTPUT_FOLDER)
    #             print(f"Directory '{settings.OUTPUT_FOLDER}' removed successfully.")
    #         else:
    #             print(f"Directory '{settings.OUTPUT_FOLDER}' does not exist.")
    #     except Exception as e:
    #         print(f"Error removing directory '{settings.OUTPUT_FOLDER}': {e}")
            
            
            
            

