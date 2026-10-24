"""``/webhooks/github`` hands issue mentions to MDA's GitHub channel where it is on."""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest

from openswe.github import forwarding
from openswe.github import routes as github_routes
from openswe.github import webhook as github_webhooks
from openswe.webhooks import common as webhook_common
from tests.conftest import register_github_logins

WEBHOOK_SECRET = "test-secret-for-webhook"
REPO = {"name": "api", "owner": {"login": "acme"}, "private": True}


def _issue(**fields: Any) -> dict[str, Any]:
    return {
        "id": 101,
        "number": 7,
        "title": "Crash on start",
        "body": "@openswe please fix this",
        "user": {"login": "ada"},
        **fields,
    }


async def _deliver(event_type: str, payload: dict[str, Any]) -> tuple[httpx.Response, bytes]:
    from openswe.api.app import app

    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": f"sha256={signature}",
        "User-Agent": "GitHub-Hookshot/abc",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/webhooks/github", content=body, headers=headers), body


@pytest.fixture
def intake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    """Open SWE's own PR dispatch, and the forward to MDA, as stand-ins."""
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(github_routes, "repo_is_routable", AsyncMock(return_value=True))
    monkeypatch.setattr(
        webhook_common, "enforce_public_repo_org_gate", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(github_routes, "_launch_automations", AsyncMock())
    register_github_logins(monkeypatch, "ada")
    monkeypatch.setenv("OPEN_SWE_MDA_CHANNELS", "linear, github")
    mocks = {"pr_comment": AsyncMock(), "issue": AsyncMock(), "forward": AsyncMock()}
    monkeypatch.setattr(github_webhooks, "process_github_pr_comment", mocks["pr_comment"])
    monkeypatch.setattr(github_webhooks, "process_github_issue", mocks["issue"])
    monkeypatch.setattr(forwarding, "forward_to_mda_channel", mocks["forward"])
    return mocks


@pytest.mark.parametrize(
    ("event_type", "payload", "message"),
    [
        (
            "issues",
            {"action": "opened", "issue": _issue(), "repository": REPO, "sender": {}},
            "Processing GitHub issue event",
        ),
        (
            "issue_comment",
            {
                "action": "created",
                "issue": _issue(body="no tag"),
                "comment": {"id": 55, "body": "@openswe try again", "user": {"login": "ada"}},
                "repository": REPO,
                "sender": {"login": "ada"},
            },
            "Processing GitHub issue comment event",
        ),
    ],
)
async def test_an_issue_mention_goes_to_mda_exactly_as_github_sent_it(
    intake: dict[str, AsyncMock], event_type: str, payload: dict[str, Any], message: str
) -> None:
    response, body = await _deliver(event_type, payload)

    assert response.json()["message"] == message
    forwarded_body, headers = intake["forward"].await_args.args
    assert forwarded_body == body
    # Only GitHub's own headers: not the User-Agent or anything else the request carried.
    assert headers == {
        "X-Hub-Signature-256": headers["X-Hub-Signature-256"],
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": "delivery-1",
        "Content-Type": "application/json",
    }
    assert headers["X-Hub-Signature-256"].startswith("sha256=")
    if event_type == "issues":
        # The backend's own side effects of the delivery still run.
        github_routes._launch_automations.assert_awaited_once()  # type: ignore[attr-defined]


async def test_an_issue_mention_stays_on_open_swe_where_the_channel_is_off(
    intake: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPEN_SWE_MDA_CHANNELS", "linear")
    payload = {"action": "opened", "issue": _issue(), "repository": REPO, "sender": {}}

    response, _ = await _deliver("issues", payload)

    assert response.json()["message"] == "Processing GitHub issue event"
    intake["forward"].assert_not_awaited()
    intake["issue"].assert_awaited_once_with(payload, "issues")


async def test_pull_request_comments_stay_on_open_swe(intake: dict[str, AsyncMock]) -> None:
    payload = {
        "action": "created",
        "issue": _issue(pull_request={"url": "https://api.github.com/pulls/7"}),
        "comment": {"id": 55, "body": "@openswe try again", "user": {"login": "ada"}},
        "repository": REPO,
        "sender": {"login": "ada"},
    }

    await _deliver("issue_comment", payload)

    intake["pr_comment"].assert_awaited_once()
    intake["forward"].assert_not_awaited()


def _mda(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> list[httpx2.Request]:
    monkeypatch.setenv("MDA_RUNTIME_URL", "https://mda.test")
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    monkeypatch.setattr(forwarding.asyncio, "sleep", AsyncMock())
    seen: list[httpx2.Request] = []
    real_client = httpx2.AsyncClient

    def recording(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    def client(**kwargs: Any) -> httpx2.AsyncClient:
        assert kwargs["follow_redirects"] is False
        return real_client(**kwargs)

    monkeypatch.setattr(forwarding.httpx2, "AsyncClient", client)
    monkeypatch.setattr(forwarding, "pooled_transport", lambda: httpx2.MockTransport(recording))
    return seen


_HEADERS = {
    "X-Hub-Signature-256": "sha256=abc",
    "X-GitHub-Event": "issues",
    "X-GitHub-Delivery": "delivery-1",
    "Content-Type": "application/json",
}


async def test_the_forward_retries_until_mda_takes_the_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([httpx2.Response(503), httpx2.Response(502), httpx2.Response(202)])
    seen = _mda(monkeypatch, lambda _request: next(answers))

    await forwarding.forward_to_mda_channel(b'{"action":"opened"}', _HEADERS)

    assert len(seen) == 3
    for request in seen:
        assert str(request.url) == "https://mda.test/channels/github/events"
        assert request.content == b'{"action":"opened"}'
        for name, value in _HEADERS.items():
            assert request.headers[name] == value


@pytest.mark.parametrize("status", [401, 307])
async def test_the_forward_does_not_retry_a_refusal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    seen = _mda(monkeypatch, lambda _request: httpx2.Response(status))

    await forwarding.forward_to_mda_channel(b"{}", _HEADERS)

    assert len(seen) == 1
    # A redirect isn't followed, so it took nothing either: both are logged.
    assert "MDA's GitHub channel refused a forwarded delivery" in caplog.messages


async def test_the_forward_gives_up_after_its_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("unreachable", request=request)

    seen = _mda(monkeypatch, unreachable)

    await forwarding.forward_to_mda_channel(b"{}", _HEADERS)

    assert len(seen) == 4
