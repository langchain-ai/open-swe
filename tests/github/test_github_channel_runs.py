"""``POST /github/channel/runs``: what MDA's GitHub channel starts for an issue mention."""

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue

from openswe import dispatch
from openswe.github import channel
from openswe.github import webhook as github_webhooks
from openswe.remote_runtime.tokens import RemoteRun, sign_runtime_token, verify_runtime_token
from openswe.threads import runtimes
from openswe.webhooks import common as webhook_common
from tests.conftest import register_github_logins

SERVICE_SECRET = "service-secret-0123456789"
THREAD = "00000000-0000-4000-8000-0000000000aa"


def _issue_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "opened",
        "issue": {
            "id": 101,
            "number": 7,
            "title": "Crash on start",
            "body": "@openswe please fix this",
            "html_url": "https://github.com/acme/api/issues/7",
            "user": {"login": "ada"},
        },
        "repository": {"name": "api", "owner": {"login": "acme"}, "private": True},
        "sender": {"login": "ada", "id": 1, "type": "User"},
    }
    payload.update(overrides)
    return payload


def _comment_payload(body: str = "@openswe try again", **issue: Any) -> dict[str, Any]:
    payload = _issue_payload(action="created")
    payload["issue"] = {**payload["issue"], "body": "No mention here", **issue}
    payload["comment"] = {"id": 55, "body": body, "user": {"login": "ada"}}
    return payload


def _event(event: str, payload: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "thread_id": THREAD,
        "delivery_id": "delivery-1",
        "event": event,
        "payload": payload,
        **fields,
    }


async def _post(body: dict[str, Any], *, secret: str | None = SERVICE_SECRET) -> httpx.Response:
    app = FastAPI()
    app.include_router(channel.router)
    headers = {} if secret is None else {"x-mda-service-secret": secret}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as client:
        return await client.post("/github/channel/runs", json=body, headers=headers)


@pytest.fixture(autouse=True)
def _gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    monkeypatch.setattr(channel, "repo_is_routable", AsyncMock(return_value=True))
    monkeypatch.setattr(
        webhook_common, "enforce_public_repo_org_gate", AsyncMock(return_value=None)
    )
    register_github_logins(monkeypatch, "ada")


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The issue run ``process_github_issue`` would start, and the thread the channel adopts."""
    run = github_webhooks.GitHubIssueRun(
        thread_id=THREAD,
        input={"messages": [{"role": "user", "content": "Fix the crash"}]},
        configurable={"source": "github", "github_login": "ada", "repo": {"owner": "acme"}},
    )
    prepare = AsyncMock(return_value=run)
    monkeypatch.setattr(github_webhooks, "prepare_github_issue_run", prepare)
    monkeypatch.setattr(channel, "adopt_channel_run", AsyncMock())
    monkeypatch.setattr(dispatch, "_run_user_id", AsyncMock(return_value="user-1"))
    monkeypatch.setattr(webhook_common, "AGENT_VERSION_METADATA", {"LANGSMITH_AGENT_VERSION": "v1"})

    async def context(
        configurable: dict[str, JsonValue], *, thread_id: str, assistant_id: str
    ) -> dict[str, JsonValue]:
        run = RemoteRun(thread_id=thread_id, assistant_id=assistant_id, configurable=configurable)
        return {"graph": assistant_id, "__run_token": sign_runtime_token(run)}

    monkeypatch.setattr(runtimes, "remote_run_context", context)
    monkeypatch.setattr(runtimes, "get_tracer_project", lambda: "open-swe-test")
    monkeypatch.setattr(runtimes.RUNTIMES, "_mda", runtimes.MdaRuntime("https://mda.test"))
    monkeypatch.setattr(runtimes.Runtimes, "mda_runtime", property(lambda self: self._mda))
    return prepare


@pytest.mark.parametrize("secret", [None, "wrong-secret"])
async def test_only_the_mda_deployment_is_answered(secret: str | None) -> None:
    response = await _post(_event("issues.opened", _issue_payload()), secret=secret)

    assert response.status_code == 401


async def test_nothing_is_answered_without_a_configured_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MDA_SERVICE_SECRET")

    response = await _post(_event("issues.opened", _issue_payload()))

    assert response.status_code == 401


async def test_an_issue_mention_starts_a_run_on_the_channels_thread(prepared: AsyncMock) -> None:
    response = await _post(_event("issues.opened", _issue_payload()))

    assert response.status_code == 200
    body = response.json()
    assert body["acts"] is True
    run = body["run"]
    assert run["input"] == {"messages": [{"role": "user", "content": "Fix the crash"}]}
    prepared.assert_awaited_once()
    assert prepared.await_args.args[1:] == ("issues", THREAD)
    channel.adopt_channel_run.assert_awaited_once_with(THREAD, "github")  # type: ignore[attr-defined]
    # The run token is signed for the channel's thread.
    token = verify_runtime_token(run["context"]["__run_token"])
    assert token.thread_id == THREAD
    assert token.configurable["github_login"] == "ada"
    assert run["context"]["graph"] == "agent"
    # Run metadata keeps only keys MDA accepts from a handler.
    metadata = run["metadata"]
    assert metadata["channel"] == "github"
    assert metadata["github_delivery"] == "delivery-1"
    assert metadata["LANGSMITH_AGENT_VERSION"] == "v1"
    assert metadata["user_id"] == "user-1"
    assert "invocation_id" in metadata
    assert not [key for key in metadata if key.startswith(("mda_", "langgraph_"))]


async def test_a_comment_mention_on_an_issue_starts_a_run(prepared: AsyncMock) -> None:
    response = await _post(_event("issue_comment.created", _comment_payload()))

    assert response.json()["run"] is not None
    assert prepared.await_args.args[1:] == ("issue_comment", THREAD)


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        # Pull request conversations stay on Open SWE's own intake.
        ("issue_comment.created", _comment_payload(pull_request={"url": "https://x"})),
        ("issues.opened", _issue_payload(issue={**_issue_payload()["issue"], "body": "no tag"})),
        ("issue_comment.created", _comment_payload(body="no tag")),
        ("issues.edited", _issue_payload(action="edited", changes={"labels": {}})),
    ],
)
async def test_events_open_swe_would_not_act_on_start_nothing(
    prepared: AsyncMock, event: str, payload: dict[str, Any]
) -> None:
    response = await _post(_event(event, payload))

    assert response.json() == {"run": None, "acts": False}
    prepared.assert_not_awaited()


async def test_an_unregistered_commenter_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, prepared: AsyncMock
) -> None:
    register_github_logins(monkeypatch)

    response = await _post(_event("issue_comment.created", _comment_payload()))

    assert response.json() == {"run": None, "acts": False}
    prepared.assert_not_awaited()


async def test_the_public_repository_gate_applies(
    monkeypatch: pytest.MonkeyPatch, prepared: AsyncMock
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "enforce_public_repo_org_gate",
        AsyncMock(return_value={"status": "ignored", "reason": "not an org member"}),
    )

    response = await _post(_event("issues.opened", _issue_payload()))

    assert response.json() == {"run": None, "acts": False}
    prepared.assert_not_awaited()


async def test_a_waiting_thread_is_only_asked_whether_open_swe_acts(prepared: AsyncMock) -> None:
    response = await _post(_event("issue_comment.created", _comment_payload(), interrupted=True))

    assert response.json() == {"run": None, "acts": True}
    # No reaction, metadata or run token while the thread waits.
    prepared.assert_not_awaited()
    channel.adopt_channel_run.assert_not_awaited()  # type: ignore[attr-defined]


async def test_a_run_the_issue_intake_declines_starts_nothing(prepared: AsyncMock) -> None:
    # For example a sender without an email mapping.
    prepared.return_value = None

    response = await _post(_event("issues.opened", _issue_payload()))

    assert response.json() == {"run": None, "acts": True}
    channel.adopt_channel_run.assert_not_awaited()  # type: ignore[attr-defined]


async def test_the_payload_must_be_the_events(prepared: AsyncMock) -> None:
    response = await _post(_event("issues.reopened", _issue_payload()))

    assert response.status_code == 422
    prepared.assert_not_awaited()
