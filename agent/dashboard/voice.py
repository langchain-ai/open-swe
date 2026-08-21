import httpx
from fastapi import HTTPException, Request

from ..config import DEFAULT_OPENAI_BASE_URL, openai_api_key, openai_base_url
from ..settings.team_settings import get_team_transcription_model

MAX_AUDIO_BYTES = 10 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {
    "audio/mp4": "audio.m4a",
    "audio/mpeg": "audio.mp3",
    "audio/ogg": "audio.ogg",
    "audio/wav": "audio.wav",
    "audio/webm": "audio.webm",
}


async def transcribe_audio(request: Request) -> str:
    content_type = request.headers.get("content-type", "").partition(";")[0].lower()
    filename = SUPPORTED_AUDIO_TYPES.get(content_type)
    if not filename:
        raise HTTPException(415, "Unsupported audio format")

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Audio recording is too large")
        chunks.append(chunk)
    if not size:
        raise HTTPException(400, "Audio recording is empty")

    api_key = openai_api_key()
    if not api_key:
        raise HTTPException(503, "Voice dictation is not configured")
    base_url = (openai_base_url() or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    model = await get_team_transcription_model()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response = await client.post(
                f"{base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": model},
                files={"file": (filename, b"".join(chunks), content_type)},
            )
        response.raise_for_status()
        text = response.json().get("text", "").strip()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Voice transcription failed") from exc
    if not text:
        raise HTTPException(422, "No speech was detected")
    return text
