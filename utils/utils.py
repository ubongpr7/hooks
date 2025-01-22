

import logging
import os
import re
import subprocess
from urllib.parse import urlparse

import requests
import boto3

from django.conf import settings


def sanitize_filename(filename):
    """
    Removes or replaces characters that are unsafe for filenames.
    """
    # Replace spaces with underscores
    filename = filename.replace(' ', '_')
    # Remove any character that is not alphanumeric, underscore, hyphen, or dot
    filename = re.sub(r'[^\w\-_\.]', '', filename)
    return filename






def create_s3_client():
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







def ffprobe_get_frame_count(video_filepath):
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
    
    
 
    
def generate_presigned_url(bucket_name, object_key, expiration=3600):
    """
    Generate a presigned URL to download the S3 object.
    object_key: aws media link 
    expiration: expiration time in seconds (default: 3600 seconds)
    """
    try:
        s3_client = create_s3_client()
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
    
    

def download_video_from_s3(s3_url, local_folder):
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
    presigned_url = generate_presigned_url(bucket_name, object_key)
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

    
    
def upload_to_s3(file_path, bucket_name, s3_key):
    """
    Uploads a file to an S3 bucket.
    file_path: The path to the local file to be uploaded
    bucket_name: The S3 bucket where the file will be uploaded
    s3_key: The key under which the file will be stored in S3
    """
    try:
        s3_client = create_s3_client()
        s3_client.upload_file(file_path, bucket_name, s3_key)
        file_url = f"https://{bucket_name}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_key}"
        logging.info(f"Uploaded preprocessed video to S3: {file_url}")
        return file_url
    except Exception as e:
        logging.error(f"Error uploading to S3: {e}")
        raise
    
    
      
        
