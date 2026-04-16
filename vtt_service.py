import asyncio
import logging
import math
import mimetypes
import os
import re
import tempfile
from pydub import AudioSegment

from openai import NOT_GIVEN, AsyncOpenAI

logger = logging.getLogger("hermes.vtt")

# Whisper API rejects uploads over 25 MiB (26_214_400 bytes). Leave headroom for
# multipart overhead so we never trip the 413 again.
MAX_WHISPER_UPLOAD_BYTES = 24 * 1024 * 1024
# Target per-chunk size when we have to split; smaller than the upload cap so
# variable bitrate wobble cannot push a chunk over the edge.
CHUNK_TARGET_BYTES = 20 * 1024 * 1024

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
TIMESTAMP_LINE_PATTERN = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})(.*)$"
)

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


def _ts_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _seconds_to_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _extract_cues(vtt: str) -> list[tuple[float, float, list[str]]]:
    lines = vtt.split("\n")
    cues: list[tuple[float, float, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = TIMESTAMP_LINE_PATTERN.match(line)
        if not m:
            i += 1
            continue
        start = _ts_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _ts_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(lines[i])
            i += 1
        cues.append((start, end, text_lines))
    return cues


def _merge_vtt_chunks(chunks: list[tuple[str, float]]) -> str:
    merged: list[tuple[float, float, list[str]]] = []
    offset = 0.0
    for vtt_text, chunk_duration in chunks:
        for start, end, text in _extract_cues(vtt_text):
            merged.append((start + offset, end + offset, text))
        offset += chunk_duration

    out = ["WEBVTT", ""]
    for idx, (start, end, text) in enumerate(merged, 1):
        out.append(str(idx))
        out.append(f"{_seconds_to_ts(start)} --> {_seconds_to_ts(end)}")
        out.extend(text)
        out.append("")
    return "\n".join(out)


# Skip anything under 0.2s — Whisper rejects <0.1s with HTTP 400.
MIN_CHUNK_MS = 200


def _split_audio_for_whisper(
    src_path: str, workdir: str, size_bytes: int
) -> list[tuple[str, float]]:
    """Return list of (chunk_path, chunk_duration_seconds).

    Uses pydub AudioSegment to slice. pydub handle container decode +
    mp3 encode via ffmpeg under hood, gives exact slice boundaries so no
    stray tail stubs.
    """
    audio = AudioSegment.from_file(src_path)
    total_ms = len(audio)
    if total_ms <= 0:
        raise RuntimeError("pydub loaded empty audio")

    num_chunks = max(2, math.ceil(size_bytes / CHUNK_TARGET_BYTES))
    seg_ms = max(1000, math.ceil(total_ms / num_chunks))

    logger.info(
        "Splitting audio: duration=%.2fs size=%d chunks=%d seg_time=%.2fs",
        total_ms / 1000, size_bytes, num_chunks, seg_ms / 1000,
    )

    # Mono 16kHz — Whisper target. Smaller chunks, predictable size.
    normalized = audio.set_channels(1).set_frame_rate(16000)

    chunks: list[tuple[str, float]] = []
    for idx, start in enumerate(range(0, total_ms, seg_ms)):
        end = min(start + seg_ms, total_ms)
        dur_ms = end - start
        if dur_ms < MIN_CHUNK_MS:
            logger.info("Skipping tail chunk %d: %dms below min", idx, dur_ms)
            continue
        path = os.path.join(workdir, f"chunk_{idx:04d}.mp3")
        normalized[start:end].export(path, format="mp3", bitrate="64k")
        chunks.append((path, dur_ms / 1000.0))

    if not chunks:
        raise RuntimeError("no usable chunks after split")
    return chunks


async def _whisper_call(filename: str, mime_type: str, data: bytes, language: str | None) -> str:
    return await client.audio.transcriptions.create(
        model="whisper-1",
        file=(filename, data, mime_type),
        language=language if language else NOT_GIVEN,
        response_format="vtt",
    )


async def _transcribe_chunk(path: str, duration: float, language: str | None) -> tuple[str, float]:
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path)
    logger.info("Transcribing chunk %s (%d bytes, %.2fs)", name, len(data), duration)
    vtt_text = await _whisper_call(name, "audio/mpeg", data, language)
    return vtt_text, duration


async def _transcribe_chunked(audio_bytes: bytes, filename: str, language: str | None) -> str:
    suffix = os.path.splitext(filename)[1] or ".bin"
    with tempfile.TemporaryDirectory() as workdir:
        src_path = os.path.join(workdir, f"source{suffix}")
        with open(src_path, "wb") as f:
            f.write(audio_bytes)

        chunks = _split_audio_for_whisper(src_path, workdir, len(audio_bytes))

        # Validate each chunk is under the upload limit before firing requests.
        for path, _dur in chunks:
            chunk_size = os.path.getsize(path)
            if chunk_size > MAX_WHISPER_UPLOAD_BYTES:
                raise RuntimeError(
                    f"Chunk {os.path.basename(path)} is {chunk_size} bytes, exceeds Whisper limit"
                )

        logger.info("Dispatching %d chunks to Whisper", len(chunks))
        results = await asyncio.gather(
            *[_transcribe_chunk(path, dur, language) for path, dur in chunks]
        )

    return _merge_vtt_chunks(results)


async def _transcribe_single(audio_bytes: bytes, filename: str, language: str | None) -> str:
    mime_type = mimetypes.guess_type(filename)[0] or "audio/mpeg"
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        tmp.seek(0)
        logger.info("Calling OpenAI Whisper API for %s", filename)
        return await _whisper_call(filename, mime_type, tmp.read(), language)


async def transcribe_to_vtt(audio_bytes: bytes, filename: str, language: str | None = None) -> str:
    size = len(audio_bytes)
    mime_type = mimetypes.guess_type(filename)[0] or "audio/mpeg"
    logger.info(
        "Transcribing %s (%d bytes, mime=%s, language=%s)",
        filename, size, mime_type, language or "auto",
    )

    if size <= MAX_WHISPER_UPLOAD_BYTES:
        transcript = await _transcribe_single(audio_bytes, filename, language)
    else:
        logger.info(
            "File %s exceeds Whisper upload limit (%d > %d), chunking",
            filename, size, MAX_WHISPER_UPLOAD_BYTES,
        )
        transcript = await _transcribe_chunked(audio_bytes, filename, language)

    logger.info("Whisper transcription complete for %s, applying capitalization rules", filename)
    result = process_vtt_content(transcript)
    logger.info("VTT processing complete for %s (%d chars)", filename, len(result))
    return result
