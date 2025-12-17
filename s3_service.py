import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import os
import uuid
from datetime import datetime, timezone

load_dotenv()

s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION"))

def create_presigned_url(bucket_name, object_name, expiration=600, method="put_object"):
    try:
        response = s3_client.generate_presigned_url(
            ClientMethod=method,
            Params={"Bucket": bucket_name, "Key": object_name},
            ExpiresIn=expiration
        )

        return response
    except ClientError as e:
        print(e)
        return None


def upload_audio_to_s3(audio_bytes: bytes, bucket_name: str, filename_download: str = "tts_output.mp3", expiration: int = 3600):
    """
    Upload audio bytes to S3 and return metadata with presigned URL.

    Args:
        audio_bytes: The audio file content as bytes
        bucket_name: S3 bucket name
        filename_download: Human-readable filename for download
        expiration: Presigned URL expiration time in seconds (default: 1 hour)

    Returns:
        dict with presignedurl, filename, storage, type, created_on, filesize, id, filename_disk, filename_download
        None if upload fails
    """
    try:
        # Generate UUID v4 for file ID

        # Create filename_disk (UUID + extension)
        filename_disk = filename_download

        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=filename_disk,
            Body=audio_bytes,
            ContentType="audio/mp3"
        )

        # Generate presigned GET URL
        presigned_url = create_presigned_url(
            bucket_name=bucket_name,
            object_name=filename_disk,
            expiration=expiration,
            method="get_object"
        )

        if not presigned_url:
            return None

        # Build response payload
        return {
            "presignedurl": presigned_url,
            "filename": filename_download,
            "storage": "s3",
            "type": "audio/mp3",
            "created_on": datetime.now(timezone.utc).isoformat(),
            "filesize": len(audio_bytes),
            "id": filename_download,
            "filename_disk": filename_disk,
            "filename_download": filename_download
        }

    except ClientError as e:
        print(f"S3 upload error: {e}")
        return None
