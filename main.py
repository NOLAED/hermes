from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import StreamingResponse
from jwt.exceptions import DecodeError
from pydantic import BaseModel
from typing import Literal, List
import elevenlabs
import os
from dotenv import load_dotenv
import jwt
import zipfile
from io import BytesIO

from typing import Annotated
from s3_service import upload_audio_to_s3


# Load environment variables
load_dotenv()

app = FastAPI()
JWT_KEY = os.getenv('JWT_KEY')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

@app.get("/")
async def root():
    return {"greeting": "Hello, World!", "message": "Welcome to FastAPI!"}


class TTSEntry(BaseModel):
    name: str | None
    text: str
    voice_id: str | None = None

class TTSRequest(BaseModel):
    format: Literal["url"] | Literal["file"]
    texts: List[TTSEntry]

@app.post('/api/v1/tts')
async def tts(request: Request, req_body: TTSRequest, x_api_key:str = Header(convert_underscores=True) ):

    # Get API key from environment
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ELEVENLABS_API_KEY not configured")
    if not x_api_key:
        raise HTTPException(status_code=403, detail="No auth token passed")

    decodedtoken = None
    try:
        decodedtoken = jwt.decode(x_api_key, key=JWT_KEY, algorithms='HS256')
    except jwt.exceptions.DecodeError:
        raise HTTPException(status_code=403, detail="Invalid token")

    if not decodedtoken: 
        raise HTTPException(status_code=500, detail="Something went wrong")

    if not decodedtoken.get('admin_access', False):
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    # Initialize ElevenLabs client
    client = elevenlabs.AsyncElevenLabs(api_key=api_key)

    # Collect all generated audio with metadata
    audio_results = []

    for tts_entry in req_body.texts:
        ttsname = uuid4() if not tts_entry.name else tts_entry.name
        # Use provided voice_id or default to a Spanish voice
        voice_id = tts_entry.voice_id or "ThT5KcBeYPX3keUQqHPh"  # Default: Paula (Spanish)
        try:
            # Generate audio using ElevenLabs
            audio_generator = client.text_to_speech.convert(
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
                "audio_bytes": audio_bytes
            })

        except Exception as e:
            # Store failure result
            audio_results.append({
                "success": False,
                "name": ttsname,
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
                        "payload": upload_result
                    })
                else:
                    results.append({
                        "success": False,
                        "message": "Failed to upload to S3"
                    })
            else:
                results.append({
                    "success": False,
                    "message": result["message"]
                })

        return results
    else:
        raise HTTPException(status_code=403, detail="Unknown format") 

