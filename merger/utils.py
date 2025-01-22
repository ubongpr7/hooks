import logging
from django.conf import settings
import boto3
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)



def download_from_s3(file_key, local_file_path):

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
