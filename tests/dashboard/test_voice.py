from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.dashboard import routes, voice


def _request(body: bytes, content_type: str = "audio/webm") -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/dashboard/api/voice/transcriptions",
            "headers": [(b"content-type", content_type.encode())],
        },
        receive,
    )


def test_voice_route_requires_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post(
        "/dashboard/api/voice/transcriptions",
        content=b"audio",
        headers={"content-type": "audio/webm", "origin": "http://testserver"},
    )
    assert response.status_code == 401


async def test_transcribe_audio_validates_and_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException, match="Unsupported audio format"):
        await voice.transcribe_audio(_request(b"audio", "text/plain"))

    monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", 4)
    with pytest.raises(HTTPException, match="too large"):
        await voice.transcribe_audio(_request(b"audio"))

    monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", 100)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    async def model() -> str:
        return "gpt-transcribe"

    monkeypatch.setattr(voice, "get_team_transcription_model", model)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer secret"
        assert b"gpt-transcribe" in request.content
        assert b"audio" in request.content
        return httpx.Response(200, json={"text": " dictated text "})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: client)
    assert await voice.transcribe_audio(_request(b"audio")) == "dictated text"
