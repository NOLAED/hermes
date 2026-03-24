from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal, List
import elevenlabs
import os
import logging
import sys
from dotenv import load_dotenv
import jwt
import zipfile
from io import BytesIO
from s3_service import upload_audio_to_s3, download_file_from_s3, upload_vtt_to_s3
from vtt_service import transcribe_to_vtt


# Load environment variables
load_dotenv()

# Configure logging for Railway (stdout)
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hermes")

app = FastAPI()
JWT_KEY = os.getenv('JWT_KEY')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

elevenlabs_client = elevenlabs.AsyncElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))


async def verify_admin_token(x_api_key: str = Header(convert_underscores=True)) -> dict:
    if not x_api_key:
        raise HTTPException(status_code=403, detail="No auth token passed")
    try:
        decoded = jwt.decode(x_api_key, key=JWT_KEY, algorithms=['HS256'])
    except jwt.exceptions.DecodeError:
        raise HTTPException(status_code=403, detail="Invalid token")
    if not decoded:
        raise HTTPException(status_code=500, detail="Something went wrong")
    if not decoded.get('admin_access', False):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return decoded


@app.get("/")
async def root():
    return {"greeting": "Hello, World!", "message": "Welcome to FastAPI!"}


class TTSEntry(BaseModel):
    name: str | None = None
    text: str
    voice_id: str | None = None
    custom_id: str | None = None

class TTSRequest(BaseModel):
    format: Literal["url"] | Literal["file"]
    texts: List[TTSEntry]

@app.post('/api/v1/tts')
async def tts(req_body: TTSRequest, _token: dict = Depends(verify_admin_token)):
    # Collect all generated audio with metadata
    audio_results = []

    for tts_entry in req_body.texts:
        ttsname = str(uuid4()) if not tts_entry.name else tts_entry.name
        # Set custom_id to default to name if not provided
        custom_id = tts_entry.custom_id or tts_entry.name
        # Use provided voice_id or default to a Spanish voice
        voice_id = tts_entry.voice_id or "ThT5KcBeYPX3keUQqHPh"  # Default: Paula (Spanish)
        try:
            # Generate audio using ElevenLabs
            audio_generator = elevenlabs_client.text_to_speech.convert(
                voice_id=voice_id,
                text=tts_entry.text,
                model_id="eleven_multilingual_v2",  # Supports Spanish
                output_format="mp3_22050_32"
            )

            # Convert generator to bytes
            audio_bytes = b"".join([b async for b in audio_generator])

            # Store successful result
            audio_results.append({
                "success": True,
                "name": ttsname,
                "custom_id": custom_id,
                "audio_bytes": audio_bytes
            })

        except Exception as e:
            # Store failure result
            audio_results.append({
                "success": False,
                "name": ttsname,
                "custom_id": custom_id,
                "message": f"ElevenLabs API error: {str(e)}"
            })

    # Handle response based on format
    if req_body.format == "file":
        # Create a zip file with all successful audio files
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for result in audio_results:
                if result["success"]:
                    filename = f"{result['name']}_es.mp3"
                    zip_file.writestr(filename, result["audio_bytes"])

        zip_buffer.seek(0)

        # Return zip file as streaming response
        return StreamingResponse(
            iter([zip_buffer.getvalue()]),
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=tts_batch.zip"
            })
    elif req_body.format == "url":
        # Upload to S3 and return presigned URLs with metadata
        if not S3_BUCKET_NAME:
            raise HTTPException(status_code=500, detail="S3_BUCKET_NAME not configured")

        results = []
        for result in audio_results:
            if result["success"]:
                upload_result = upload_audio_to_s3(
                    audio_bytes=result["audio_bytes"],
                    bucket_name=S3_BUCKET_NAME,
                    filename_download=result['name'],
                    expiration=3600  # 1 hour
                )

                if upload_result:
                    results.append({
                        "success": True,
                        "custom_id": result["custom_id"],
                        "payload": upload_result
                    })
                else:
                    results.append({
                        "success": False,
                        "custom_id": result["custom_id"],
                        "message": "Failed to upload to S3"
                    })
            else:
                results.append({
                    "success": False,
                    "custom_id": result["custom_id"],
                    "message": result["message"]
                })

        return results
    else:
        raise HTTPException(status_code=403, detail="Unknown format")


class VTTRequest(BaseModel):
    format: Literal["url"] | Literal["file"]
    type: str
    filename_disk: str
    uploaded_on: str

@app.post('/api/v1/vtt')
async def vtt(req_body: VTTRequest, _token: dict = Depends(verify_admin_token)):
    logger.info("VTT request received: filename_disk=%s, format=%s, type=%s", req_body.filename_disk, req_body.format, req_body.type)

    if not os.getenv("OPENAI_API_KEY"):
        logger.error("VTT request failed: OPENAI_API_KEY not configured")
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    if not S3_BUCKET_NAME:
        logger.error("VTT request failed: S3_BUCKET_NAME not configured")
        raise HTTPException(status_code=500, detail="S3_BUCKET_NAME not configured")

    # Download audio from S3
    try:
        logger.info("Downloading audio from S3: bucket=%s, key=%s", S3_BUCKET_NAME, req_body.filename_disk)
        audio_bytes = download_file_from_s3(S3_BUCKET_NAME, req_body.filename_disk)
        logger.info("Downloaded audio from S3: %d bytes", len(audio_bytes))
    except Exception as e:
        logger.error("Failed to download from S3: %s", str(e))
        raise HTTPException(status_code=404, detail=f"Failed to download from S3: {str(e)}")

    # Transcribe to VTT
    try:
        logger.info("Starting transcription for %s", req_body.filename_disk)
        vtt_content = await transcribe_to_vtt(audio_bytes, req_body.filename_disk)
        logger.info("Transcription complete: %d characters", len(vtt_content))
    except Exception as e:
        logger.error("Transcription failed for %s: %s", req_body.filename_disk, str(e))
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    vtt_bytes = vtt_content.encode("utf-8")
    vtt_filename = os.path.splitext(req_body.filename_disk)[0] + ".vtt"

    if req_body.format == "file":
        logger.info("Returning VTT as file download: %s (%d bytes)", vtt_filename, len(vtt_bytes))
        return StreamingResponse(
            iter([vtt_bytes]),
            media_type="text/vtt",
            headers={"Content-Disposition": f"attachment; filename={vtt_filename}"},
        )
    elif req_body.format == "url":
        logger.info("Uploading VTT to S3: %s", vtt_filename)
        upload_result = upload_vtt_to_s3(
            vtt_bytes=vtt_bytes,
            bucket_name=S3_BUCKET_NAME,
            filename=vtt_filename,
            expiration=3600,
        )

        if not upload_result:
            logger.error("Failed to upload VTT to S3: %s", vtt_filename)
            raise HTTPException(status_code=500, detail="Failed to upload VTT to S3")

        logger.info("VTT uploaded to S3 successfully: %s", vtt_filename)
        return {"success": True, "payload": upload_result}
    else:
        logger.warning("VTT request rejected: unknown format=%s", req_body.format)
        raise HTTPException(status_code=403, detail="Unknown format")

