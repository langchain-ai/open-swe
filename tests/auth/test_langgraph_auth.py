"""The LangGraph API only answers Open SWE's own callers."""

import pytest
from langgraph_sdk import Auth

from agent import langgraph_auth


async def _auth(headers=None, authorization=None):
    return await langgraph_auth.authenticate(headers=headers, authorization=authorization)


async def _rejected(headers=None, authorization=None) -> int:
    with pytest.raises(Auth.exceptions.HTTPException) as info:
        await _auth(headers, authorization)
    return info.value.status_code


async def test_backend_api_key_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_backend")
    assert await _auth({b"x-api-key": b"lsv2_pt_backend"}) == {"identity": "open-swe-backend"}
    # header names arrive lowercased as bytes from the ASGI scope; be tolerant of str too
    assert await _auth({"X-Api-Key": "lsv2_pt_backend"}) == {"identity": "open-swe-backend"}


async def test_desktop_bearer_token_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_LOCAL_AUTH_TOKEN", "desktop-secret")
    assert await _auth(None, "Bearer desktop-secret") == {"identity": "local-user"}
    assert await _auth(None, "bearer desktop-secret") == {"identity": "local-user"}


async def test_wrong_or_missing_credentials_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_backend")
    monkeypatch.setenv("OPEN_SWE_LOCAL_AUTH_TOKEN", "desktop-secret")
    assert await _rejected() == 401
    assert await _rejected({b"x-api-key": b"lsv2_pt_other"}) == 401
    assert await _rejected(None, "Bearer wrong") == 401
    assert await _rejected(None, "Basic desktop-secret") == 401
    assert await _rejected(None, "desktop-secret") == 401
    assert await _rejected({b"x-auth-scheme": b"langsmith"}) == 401


async def test_nothing_configured_rejects_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_SWE_LOCAL_AUTH_TOKEN", raising=False)
    assert await _rejected({b"x-api-key": b""}) == 401
    assert await _rejected(None, "Bearer ") == 401
    assert await _rejected({b"x-api-key": b"anything"}, "Bearer anything") == 401


def test_desktop_configs_point_at_the_handler() -> None:
    import json
    from pathlib import Path

    for path in ("langgraph.json", "langgraph.desktop.json", "tests/e2e/langgraph.desktop.json"):
        assert json.loads(Path(path).read_text())["auth"]["path"] == "agent.langgraph_auth:auth", (
            path
        )
