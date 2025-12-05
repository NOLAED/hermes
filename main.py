from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import StreamingResponse
from jwt.exceptions import DecodeError
from pydantic import BaseModel
from typing import Literal
import elevenlabs
import os
from dotenv import load_dotenv
import jwt

from typing import Annotated


# Load environment variables
load_dotenv()

app = FastAPI()
JWT_KEY = os.getenv('JWT_KEY')

@app.get("/")
async def root():
    return {"greeting": "Hello, World!", "message": "Welcome to FastAPI!"}



class TTSRequest(BaseModel):
    text: str
    format: Literal["url"] | Literal["file"]
    voice_id: str | None = None  # Optional: Spanish voice ID from ElevenLabs

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
    client = elevenlabs.ElevenLabs(api_key=api_key)

    # Use provided voice_id or default to a Spanish voice
    voice_id = req_body.voice_id or "ThT5KcBeYPX3keUQqHPh"  # Default: Paula (Spanish)

    try:
        # Generate audio using ElevenLabs
        audio_generator = client.text_to_speech.convert(
            voice_id=voice_id,
            text=req_body.text,
            model_id="eleven_multilingual_v2",  # Supports Spanish
            output_format="mp3_22050_32"  
        )

        # Convert generator to bytes
        audio_bytes = b"".join(audio_generator)

        # Handle response based on format
        if req_body.format == "file":
            # Return audio bytes as streaming response
            return StreamingResponse(
                iter([audio_bytes]),
                media_type="audio/mp3",
                headers={
                    "Content-Disposition": "attachment; filename=tts_output.mp3"
                }
            )
        else:  # format == "url"
            # Placeholder for URL format (to be implemented later)
            return {"message": "URL format not yet implemented", "audio_size": len(audio_bytes)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ElevenLabs API error: {str(e)}")
