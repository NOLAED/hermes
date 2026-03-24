import logging

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("hermes.s3")

load_dotenv()

s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION"))

def download_file_from_s3(bucket_name: str, key: str) -> bytes:
    logger.info("Downloading from S3: bucket=%s, key=%s", bucket_name, key)
    response = s3_client.get_object(Bucket=bucket_name, Key=key)
    data = response["Body"].read()
    logger.info("Downloaded %d bytes from S3: %s", len(data), key)
    return data


def upload_vtt_to_s3(vtt_bytes: bytes, bucket_name: str, filename: str, expiration: int = 3600):
    try:
        logger.info("Uploading VTT to S3: bucket=%s, key=%s, size=%d bytes", bucket_name, filename, len(vtt_bytes))

        s3_client.put_object(
            Bucket=bucket_name,
            Key=filename,
            Body=vtt_bytes,
            ContentType="text/vtt",
        )
        logger.info("VTT uploaded to S3 successfully: %s", filename)

        presigned_url = create_presigned_url(
            bucket_name=bucket_name,
            object_name=filename,
            expiration=expiration,
            method="get_object",
        )

        if not presigned_url:
            logger.error("Failed to generate presigned URL for VTT: %s", filename)
            return None

        return {
            "presignedurl": presigned_url,
            "storage": "s3",
            "type": "text/vtt",
            "created_on": datetime.now(timezone.utc).isoformat(),
            "filesize": len(vtt_bytes),
            "id": uuid.uuid4(),
            "filename_disk": filename,
            "filename_download": filename,
        }
    except ClientError as e:
        logger.error("S3 VTT upload error for %s: %s", filename, e)
        return None


def create_presigned_url(bucket_name, object_name, expiration=600, method="put_object"):
    try:
        response = s3_client.generate_presigned_url(
            ClientMethod=method,
            Params={"Bucket": bucket_name, "Key": object_name},
            ExpiresIn=expiration
        )

        return response
    except ClientError as e:
        logger.error("Failed to generate presigned URL: %s", e)
        return None


def upload_audio_to_s3(audio_bytes: bytes, bucket_name: str, filename_download: str, expiration: int = 3600):
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
        filename = f"{filename_download}_es.mp3"

        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=filename,
            Body=audio_bytes,
            ContentType="audio/mp3"
        )

        # Generate presigned GET URL
        presigned_url = create_presigned_url(
            bucket_name=bucket_name,
            object_name=filename,
            expiration=expiration,
            method="get_object"
        )

        if not presigned_url:
            return None

        # Build response payload
        return {
            "presignedurl": presigned_url,
            "storage": "s3",
            "type": "audio/mp3",
            "created_on": datetime.now(timezone.utc).isoformat(),
            "filesize": len(audio_bytes),
            "id": uuid.uuid4(),
            "filename_disk": filename,
            "filename_download": filename 
        }

    except ClientError as e:
        logger.error("S3 audio upload error for %s: %s", filename, e)
        return None
