"""Speech-to-text for the composer's microphone button."""

from typing import Any

from fastapi import APIRouter, Request

from ..authz import SESSION
from ..voice import transcribe_audio

router = APIRouter()


@router.post("/voice/transcriptions")
async def create_voice_transcription(
    request: Request, session: dict[str, Any] = SESSION
) -> dict[str, str]:
    return {"text": await transcribe_audio(request)}
