from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from agent import webapp
from agent.utils.webex import (
    format_webex_messages_for_prompt,
    strip_bot_mention,
    verify_webex_signature,
)
from agent.webapp import generate_thread_id_from_webex

_TEST_WEBHOOK_SECRET = "test-webex-secret"


def _sign_body_webex(body: bytes, secret: str = _TEST_WEBHOOK_SECRET) -> str:
    """Compute the X-Spark-Signature header (HMAC-SHA1)."""
    return hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()


def _post_webex_webhook(client: TestClient, payload: dict, secret: str = _TEST_WEBHOOK_SECRET):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/webhooks/webex",
        content=body,
        headers={
            "X-Spark-Signature": _sign_body_webex(body, secret),
            "Content-Type": "application/json",
        },
    )


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_verify_webex_signature_valid() -> None:
    body = b'{"test": "payload"}'
    secret = "mysecret"
    sig = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    assert verify_webex_signature(body, sig, secret) is True


def test_verify_webex_signature_invalid() -> None:
    body = b'{"test": "payload"}'
    assert verify_webex_signature(body, "badsig", "mysecret") is False


def test_verify_webex_signature_empty_secret() -> None:
    assert verify_webex_signature(b"body", "sig", "") is False


def test_verify_webex_signature_empty_signature() -> None:
    assert verify_webex_signature(b"body", "", "secret") is False


# ---------------------------------------------------------------------------
# Thread ID generation
# ---------------------------------------------------------------------------


def test_generate_thread_id_from_webex_is_deterministic() -> None:
    first = generate_thread_id_from_webex("room123", "msg456")
    second = generate_thread_id_from_webex("room123", "msg456")
    assert first == second
    assert len(first) == 36


def test_generate_thread_id_from_webex_differs_by_room() -> None:
    id_a = generate_thread_id_from_webex("roomA", "msg1")
    id_b = generate_thread_id_from_webex("roomB", "msg1")
    assert id_a != id_b


def test_generate_thread_id_from_webex_differs_by_parent() -> None:
    id_a = generate_thread_id_from_webex("room1", "parentA")
    id_b = generate_thread_id_from_webex("room1", "parentB")
    assert id_a != id_b


# ---------------------------------------------------------------------------
# Bot mention stripping
# ---------------------------------------------------------------------------


def test_strip_bot_mention_removes_email(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.utils import webex

    monkeypatch.setattr(webex, "WEBEX_BOT_EMAIL", "open-swe@webex.bot")
    assert strip_bot_mention("open-swe@webex.bot please help") == "please help"


def test_strip_bot_mention_removes_at_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.utils import webex

    monkeypatch.setattr(webex, "WEBEX_BOT_EMAIL", "open-swe@webex.bot")
    assert strip_bot_mention("@open-swe please help") == "please help"


def test_strip_bot_mention_empty() -> None:
    assert strip_bot_mention("") == ""


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


def test_format_webex_messages_for_prompt_basic() -> None:
    messages = [
        {"text": "hello there", "personEmail": "alice@example.com"},
        {"text": "please fix the bug", "personEmail": "bob@example.com"},
    ]
    result = format_webex_messages_for_prompt(messages)
    assert "alice@example.com: hello there" in result
    assert "bob@example.com: please fix the bug" in result


def test_format_webex_messages_for_prompt_empty() -> None:
    assert format_webex_messages_for_prompt([]) == "(no thread messages available)"


def test_format_webex_messages_for_prompt_missing_text() -> None:
    messages = [{"personEmail": "alice@example.com"}]
    result = format_webex_messages_for_prompt(messages)
    assert "[non-text message]" in result


# ---------------------------------------------------------------------------
# Repo config parsing
# ---------------------------------------------------------------------------


def test_get_webex_repo_config_repo_colon_syntax() -> None:
    config = webapp._get_webex_repo_config("please fix repo:my-org/my-repo bug")
    assert config == {"owner": "my-org", "name": "my-repo"}


def test_get_webex_repo_config_repo_space_syntax() -> None:
    config = webapp._get_webex_repo_config("check repo my-org/my-repo now")
    assert config == {"owner": "my-org", "name": "my-repo"}


def test_get_webex_repo_config_github_url() -> None:
    config = webapp._get_webex_repo_config("see https://github.com/langchain-ai/open-swe/issues/1")
    assert config == {"owner": "langchain-ai", "name": "open-swe"}


def test_get_webex_repo_config_repo_beats_github_url() -> None:
    config = webapp._get_webex_repo_config(
        "see https://github.com/langchain-ai/open-swe but use repo:other-org/other-repo"
    )
    assert config == {"owner": "other-org", "name": "other-repo"}


def test_get_webex_repo_config_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "WEBEX_REPO_OWNER", "default-owner")
    monkeypatch.setattr(webapp, "WEBEX_REPO_NAME", "default-repo")
    config = webapp._get_webex_repo_config("please fix the bug")
    assert config == {"owner": "default-owner", "name": "default-repo"}


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


def test_webex_webhook_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "WEBEX_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    client = TestClient(webapp.app)

    body = json.dumps({"resource": "messages", "event": "created", "data": {}}).encode()
    response = client.post(
        "/webhooks/webex",
        content=body,
        headers={
            "X-Spark-Signature": "invalidsig",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_webex_webhook_ignores_non_message_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "WEBEX_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    client = TestClient(webapp.app)

    response = _post_webex_webhook(
        client,
        {
            "resource": "rooms",
            "event": "created",
            "data": {},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webex_webhook_ignores_bot_own_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "WEBEX_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(webapp, "WEBEX_BOT_EMAIL", "open-swe@webex.bot")
    client = TestClient(webapp.app)

    response = _post_webex_webhook(
        client,
        {
            "resource": "messages",
            "event": "created",
            "data": {"personEmail": "open-swe@webex.bot"},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert "bot" in response.json()["reason"].lower()


def test_webex_webhook_accepts_valid_mention(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    async def fake_process_webex_mention(data: dict, repo_config: dict) -> None:
        called["data"] = data
        called["repo_config"] = repo_config

    monkeypatch.setattr(webapp, "WEBEX_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(webapp, "WEBEX_BOT_EMAIL", "open-swe@webex.bot")
    monkeypatch.setattr(webapp, "process_webex_mention", fake_process_webex_mention)
    client = TestClient(webapp.app)

    response = _post_webex_webhook(
        client,
        {
            "resource": "messages",
            "event": "created",
            "data": {
                "id": "msg123",
                "roomId": "room456",
                "personEmail": "alice@example.com",
                "text": "repo:my-org/my-repo fix the bug",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_webex_webhook_verify_endpoint() -> None:
    client = TestClient(webapp.app)
    response = client.get("/webhooks/webex")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
