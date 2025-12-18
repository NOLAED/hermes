---
title: FastAPI
description: A modern, fast web framework for building APIs with Python
tags:
  - python
  - fastapi
---

# Python FastAPI Example

This is a [FastAPI](https://fastapi.tiangolo.com/) app that serves a simple JSON response.

## ✨ Features

- Python
- FastAPI

## 💁‍♀️ How to use

- Install Python requirements `pip install -r requirements.txt`
- Configure environment variables (see below)
- Start the server for development `python3 main.py`

## 🔐 Environment Variables

Create a `.env` file in the root directory with the following variables:

```env
# Required: ElevenLabs API key for text-to-speech
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here

# Required: JWT secret key for token signing
JWT_KEY=your_jwt_secret_key_here


# Optional: To active the 'url' format which will upload to s3
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=<>
AWS_SECRET_ACCESS_KEY=<>
S3_BUCKET_NAME=<>
```

**Important:** Never commit your `.env` file to version control. It should be listed in `.gitignore`.

## 📡 API Documentation

### POST `/api/v1/tts`

Generate text-to-speech audio from text using ElevenLabs.

#### Authentication

Requires a JWT token with `admin_access: true` passed in the `X-API-Key` header.

#### Request Body

```json
{
  "format": "url" | "file",
  "texts": [
    {
      "name": "string (optional)",
      "text": "string (required)",
      "voice_id": "string (optional)",
      "custom_id": "string (optional)"
    }
  ]
}
```

#### Parameters

- **format**: Output format
  - `"url"`: Upload to S3 and return presigned URLs
  - `"file"`: Return a ZIP file containing all audio files

- **texts**: Array of text entries to convert to speech
  - **name** (optional): Filename for the generated audio (defaults to UUID if not provided)
  - **text** (required): The text to convert to speech
  - **voice_id** (optional): ElevenLabs voice ID (defaults to "ThT5KcBeYPX3keUQqHPh" - Paula Spanish voice)
  - **custom_id** (optional): User-supplied identifier to help distinguish one file from another. **Defaults to the value of `name` if not provided**. This field is returned in all API responses to help you track which result corresponds to which request.

#### Response

**Format: "url"**

Returns an array of results with presigned S3 URLs:

```json
[
  {
    "success": true,
    "custom_id": "my-custom-identifier",
    "payload": {
      "url": "https://...",
      "key": "...",
      "filename": "..."
    }
  }
]
```

**Format: "file"**

Returns a ZIP file (`application/zip`) containing all generated audio files.

#### Example Usage

```bash
curl -X POST http://localhost:8000/api/v1/tts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_jwt_token" \
  -d '{
    "format": "url",
    "texts": [
      {
        "name": "greeting",
        "text": "Hola, ¿cómo estás?",
        "custom_id": "user-123-greeting"
      },
      {
        "name": "farewell",
        "text": "Hasta luego",
        "custom_id": "user-123-farewell"
      }
    ]
  }'
```
