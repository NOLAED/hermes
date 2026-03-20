import mimetypes
import os
import re
import tempfile

from openai import NOT_GIVEN, AsyncOpenAI

CAPITALIZATION_RULES = {
    "student module notebook": "Student Module Notebook",
    "student module": "Student Module",
    "notebook": "Notebook",
    "module": "Module",
    "api": "API",
    "url": "URL",
    "html": "HTML",
    "css": "CSS",
    "javascript": "JavaScript",
    "python": "Python",
    "sql": "SQL",
    "xml": "XML",
    "json": "JSON",
    "pdf": "PDF",
    "csv": "CSV",
    "zoom": "Zoom",
    "google": "Google",
    "microsoft": "Microsoft",
    "apple": "Apple",
    "amazon": "Amazon",
    "facebook": "Facebook",
    "twitter": "Twitter",
    "linkedin": "LinkedIn",
    "youtube": "YouTube",
    "github": "GitHub",
}

TIMESTAMP_PATTERN = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}")
CUE_ID_PATTERN = re.compile(r"^\d+$")
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def apply_capitalization_rules(text: str) -> str:
    sorted_rules = sorted(CAPITALIZATION_RULES.items(), key=lambda x: len(x[0]), reverse=True)
    for term, proper_form in sorted_rules:
        pattern = r"\b" + re.escape(term) + r"\b"
        text = re.sub(pattern, proper_form, text, flags=re.IGNORECASE)
    return text


def process_vtt_content(vtt_content: str) -> str:
    lines = vtt_content.split("\n")
    processed = []
    for line in lines:
        stripped = line.strip()
        if stripped == "" or "WEBVTT" in stripped or TIMESTAMP_PATTERN.search(stripped) or CUE_ID_PATTERN.match(stripped):
            processed.append(line)
        else:
            processed.append(apply_capitalization_rules(line))
    return "\n".join(processed)


async def transcribe_to_vtt(audio_bytes: bytes, filename: str, language: str | None=None) -> str:
    mime_type = mimetypes.guess_type(filename)[0] or "audio/mpeg"

    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        tmp.seek(0)

        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, tmp, mime_type),
            language=language if language else NOT_GIVEN,
            response_format="vtt",
        )

    return process_vtt_content(transcript)
