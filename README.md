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
