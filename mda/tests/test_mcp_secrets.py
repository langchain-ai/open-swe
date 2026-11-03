import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import JsonValue
from rt.browser import ReportedBrowserMiddleware
from rt.session_handoff import INSTALLED, HeldSessions
from runtime_api.mcp_secrets import McpResult, Sessions

_ACCESS = "eyJ" + "a" * 24 + "." + "b" * 24 + "." + "c" * 16
# Supabase's refresh tokens are 12 characters.
_REFRESH = "y7mhzt5lpf2k"
_PROVIDER = "gho_" + "p" * 36


def _session(host: str = "beta.smith.langchain.com") -> dict[str, JsonValue]:
    return {
        "host": host,
        "storage_key": "sb-example-auth-token",
        "session": {
            "access_token": _ACCESS,
            "refresh_token": _REFRESH,
            "provider_token": _PROVIDER,
            "providerRefreshToken": "q" * 30,
        },
    }


def test_a_session_is_held_by_handle_and_no_copy_of_its_credentials_returned() -> None:
    session = _session()
    content = [
        {"type": "text", "text": json.dumps(session)},
        {"type": "text", "text": f"Signed in; refresh_token: {_REFRESH}, {_PROVIDER}"},
    ]
    held: Sessions = {}
    returned, artifact = McpResult(content, {"structured_content": session}).withheld(held)
    visible = json.dumps([returned, artifact])
    for secret in (_ACCESS, _REFRESH, _PROVIDER, "q" * 30):
        assert secret not in visible
    assert isinstance(returned, list) and isinstance(returned[0], dict)
    handle = json.loads(returned[0]["text"])["session_handle"]
    assert held == {handle: session}
    # A session only the artifact carried is named to the model all the same.
    held.clear()
    summary = [{"type": "text", "text": "Minted a session."}]
    returned, _ = McpResult(summary, {"structured_content": session}).withheld(held)
    assert isinstance(returned, list) and isinstance(returned[-1], dict)
    assert json.loads(returned[-1]["text"]) == {"session_handle": next(iter(held))}


def test_results_without_credentials_pass_through_unchanged() -> None:
    code = "def f(self):\n    access_token = self._get_access_token()\n    return access_token\n"
    for content in (
        [{"type": "text", "text": code}],
        [{"type": "text", "text": '{"access_token": null, "nextPageToken": "abc123def456ghi"}'}],
    ):
        assert McpResult(content, None).withheld({}) == (content, None)


class _Held:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, sessions: Sessions) -> None:
        self.sessions = HeldSessions(sessions)
        monkeypatch.setattr(HeldSessions, "of", classmethod(lambda _cls, _backend: self.sessions))


async def test_a_held_session_signs_in_once_and_only_on_langsmith(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Held(monkeypatch, {"elsewhere": _session("evil.example.com"), "ours": _session()})
    middleware = ReportedBrowserMiddleware(MagicMock())
    on_page = AsyncMock(return_value=INSTALLED)
    monkeypatch.setattr(middleware, "on_page", on_page)
    runtime = MagicMock()

    assert "not a LangSmith site" in await middleware._sign_in(runtime, "elsewhere")
    assert await middleware._sign_in(runtime, "ours") == INSTALLED
    for used in ("elsewhere", "ours", "never-minted"):
        assert "Unknown or used" in await middleware._sign_in(runtime, used)
    on_page.assert_awaited_once()
