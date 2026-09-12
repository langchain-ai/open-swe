import json

import httpx2
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


def _app(monkeypatch: pytest.MonkeyPatch, *, authenticated: bool = False) -> FastAPI:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    app = FastAPI()
    app.include_router(routes.router)
    if authenticated:
        app.dependency_overrides[routes.require_session] = lambda: {"sub": "octocat"}
    return app


def test_voice_routes_require_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_app(monkeypatch))
    headers = {"origin": "http://testserver"}
    transcription = client.post(
        "/dashboard/api/voice/transcriptions",
        content=b"audio",
        headers={**headers, "content-type": "audio/webm"},
    )
    session = client.post(
        "/dashboard/api/voice/session",
        json={"sdp": "v=0"},
        headers=headers,
    )
    assert transcription.status_code == 401
    assert session.status_code == 401


def test_live_session_route_validates_bounded_sdp_and_same_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_app(monkeypatch, authenticated=True))
    assert (
        client.post(
            "/dashboard/api/voice/session",
            json={"sdp": "v=0"},
            headers={"origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/dashboard/api/voice/session",
            json={"sdp": "   "},
            headers={"origin": "http://testserver"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/dashboard/api/voice/session",
            json={"sdp": "é" * (voice.MAX_SDP_BYTES // 2 + 1)},
            headers={"origin": "http://testserver"},
        ).status_code
        == 422
    )


def test_live_session_route_returns_provider_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create(offer: voice.LiveSessionOffer) -> dict[str, object]:
        assert offer.sdp == "v=0\r\n"
        return {
            "session": {"id": "live_123"},
            "transport": {"type": "webrtc", "sdp": "answer"},
        }

    monkeypatch.setattr(routes, "create_live_session", create)
    response = TestClient(_app(monkeypatch, authenticated=True)).post(
        "/dashboard/api/voice/session",
        json={"sdp": "v=0\r\n"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 201
    assert response.json() == {
        "session": {"id": "live_123"},
        "transport": {"type": "webrtc", "sdp": "answer"},
    }


async def test_create_live_session_uses_server_key_and_expected_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "server-secret")

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url == "https://api.openai.com/v1/live/sessions"
        assert request.headers["authorization"] == "Bearer server-secret"
        payload = json.loads(request.content)
        assert payload["transport"] == {"type": "webrtc", "sdp": "v=0"}
        session = payload["session"]
        assert session["model"] == "gpt-live-1"
        responses = session["delegation"]["responses"]
        assert responses["model"] == "gpt-5.6-terra"
        assert responses["parallel_tool_calls"] is False
        assert [tool["name"] for tool in responses["tools"]] == [
            "list_threads",
            "get_thread",
            "create_thread",
            "send_message",
            "stop_thread",
            "navigate_to_thread",
        ]
        return httpx2.Response(
            201,
            json={
                "session": {"id": "live_123"},
                "transport": {"type": "webrtc", "sdp": "answer"},
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **_: client)
    result = await voice.create_live_session(voice.LiveSessionOffer(sdp="v=0"))
    assert result["transport"]["sdp"] == "answer"
    assert "server-secret" not in str(result)


async def test_create_live_session_handles_configuration_and_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(HTTPException) as missing:
        await voice.create_live_session(voice.LiveSessionOffer(sdp="v=0"))
    assert missing.value.status_code == 503

    monkeypatch.setenv("OPENAI_API_KEY", "server-secret")

    def rejected(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": {"message": "sensitive provider detail"}})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(rejected))
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **_: client)
    with pytest.raises(HTTPException) as failed:
        await voice.create_live_session(voice.LiveSessionOffer(sdp="v=0"))
    assert failed.value.status_code == 502
    assert failed.value.detail == "GPT Live session creation failed"
    assert "sensitive" not in failed.value.detail


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

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url == "https://api.openai.com/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer secret"
        assert b"gpt-transcribe" in request.content
        assert b"audio" in request.content
        return httpx2.Response(200, json={"text": " dictated text "})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **_: client)
    assert await voice.transcribe_audio(_request(b"audio")) == "dictated text"
