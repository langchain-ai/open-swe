"""``POST /github/channel/events``: what MDA's GitHub channel starts for an issue mention."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from standardwebhooks import Webhook

from openswe import dispatch
from openswe.agent_api.tokens import verify_runtime_token
from openswe.github import channel
from openswe.github import webhook as github_webhooks
from openswe.webhooks import common as webhook_common
from tests.conftest import register_github_logins
from tests.mda_helpers import mda_starts, stub_mda_runtime

# As `openssl rand -hex 32` makes one: valid base64, as Standard Webhooks takes it.
SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value
THREAD = "00000000-0000-4000-8000-0000000000aa"


def _issue_payload(**overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
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


def _comment_payload(body: str = "@openswe try again", **issue: JsonValue) -> dict[str, JsonValue]:
    payload = _issue_payload(action="created")
    payload["issue"] = {**payload["issue"], "body": "No mention here", **issue}
    payload["comment"] = {"id": 55, "body": body, "user": {"login": "ada"}}
    return payload


def _event(
    event: str, payload: dict[str, JsonValue], *, interrupted: bool = False
) -> dict[str, JsonValue]:
    """What MDA's channel POSTs its endpoint."""
    thread: dict[str, JsonValue] = {"id": THREAD, "channel": {"name": "github"}}
    if interrupted:
        thread["interrupted"] = True
    return {
        "event": {"key": event, "deliveryId": "delivery-1", "payload": payload},
        "thread": thread,
    }


async def _post(body: dict[str, JsonValue], *, signed: bool = True) -> httpx.Response:
    """POST ``body`` as MDA does, signed with the shared secret."""
    app = FastAPI()
    app.include_router(channel.router)
    content = json.dumps(body)
    headers = {"content-type": "application/json"}
    if signed:
        now = datetime.now(tz=UTC)
        headers |= {
            "webhook-id": "msg_delivery-1",
            "webhook-timestamp": str(int(now.timestamp())),
            "webhook-signature": Webhook(SERVICE_SECRET).sign("msg_delivery-1", now, content),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as client:
        return await client.post("/github/channel/events", content=content, headers=headers)


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

    async def adopt_then_prepare(
        *_args: object, adopt: Callable[[str], Awaitable[None]] | None = None
    ) -> object:
        # The real one adopts the thread before it writes the thread's metadata.
        if adopt is not None and prepare.return_value is not None:
            await adopt(THREAD)
        return prepare.return_value

    prepare = AsyncMock(side_effect=adopt_then_prepare, return_value=run)
    monkeypatch.setattr(github_webhooks, "prepare_github_issue_run", prepare)
    monkeypatch.setattr(channel, "adopt_channel_run", AsyncMock())
    monkeypatch.setattr(channel, "channel_run_starting", AsyncMock())
    monkeypatch.setattr(
        dispatch,
        "_run_user_metadata",
        AsyncMock(
            return_value={
                "ls_repo_provider": "github",
                "user_id": "user-1",
                "ls_attribution_identifier": "ada",
            }
        ),
    )
    monkeypatch.setattr(webhook_common, "AGENT_VERSION_METADATA", {"LANGSMITH_AGENT_VERSION": "v1"})
    stub_mda_runtime(monkeypatch)
    return prepare


async def test_only_calls_mda_signed_are_answered(prepared: AsyncMock) -> None:
    response = await _post(_event("issues.opened", _issue_payload()), signed=False)

    assert response.status_code == 401
    prepared.assert_not_awaited()


@pytest.mark.parametrize(
    ("event", "payload"),
    [("issues.opened", _issue_payload()), ("issue_comment.created", _comment_payload())],
)
async def test_an_issue_mention_starts_a_run_on_the_channels_thread(
    prepared: AsyncMock, monkeypatch: pytest.MonkeyPatch, event: str, payload: dict[str, JsonValue]
) -> None:
    webhook = "https://backend.example/webhooks/run-complete?token=t"
    monkeypatch.setattr(channel, "COMPLETION_WEBHOOK_URL", webhook)

    response = await _post(_event(event, payload))

    assert response.status_code == 200
    run = response.json()
    assert run["input"] == {"messages": [{"role": "user", "content": "Fix the crash"}]}
    prepared.assert_awaited_once()
    assert prepared.await_args.args[1:] == (event.split(".")[0], THREAD)
    # The run completes through the backend's run-complete webhook.
    assert run["webhook"] == webhook
    channel.adopt_channel_run.assert_awaited_once_with(THREAD, "github")  # type: ignore[attr-defined]
    # Busy only once the run is ready, so a failure on the way never leaves it busy.
    channel.channel_run_starting.assert_awaited_once_with(THREAD)  # type: ignore[attr-defined]
    # The run token is signed for the channel's thread.
    token = verify_runtime_token(run["context"]["__run_token"])
    assert token.thread_id == THREAD
    assert token.configurable["github_login"] == "ada"
    assert run["context"]["graph"] == "agent"
    # The attribution the run's hooks read rides in its token.
    assert token.metadata["ls_attribution_identifier"] == "ada"
    # MDA starts the run: the answer sets none of the metadata keys MDA keeps.
    metadata = mda_starts(run)["metadata"]
    assert metadata == run["metadata"]
    assert metadata["channel"] == "github"
    assert metadata["github_delivery"] == "delivery-1"
    assert metadata["LANGSMITH_AGENT_VERSION"] == "v1"
    assert "invocation_id" in metadata
    assert not {"user_id", "ls_repo_provider", "ls_attribution_identifier"} & metadata.keys()


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        # Pull request conversations stay on Open SWE's own intake.
        ("issue_comment.created", _comment_payload(pull_request={"url": "https://x"})),
        ("issues.opened", _issue_payload(issue={**_issue_payload()["issue"], "body": "no tag"})),
        # Another deployment's handle is not this one's.
        ("issue_comment.created", _comment_payload(body="@openswe-preview try again")),
        ("issues.edited", _issue_payload(action="edited", changes={"labels": {}})),
    ],
)
async def test_events_open_swe_would_not_act_on_start_nothing(
    prepared: AsyncMock, event: str, payload: dict[str, JsonValue]
) -> None:
    response = await _post(_event(event, payload))

    assert response.json() is None
    prepared.assert_not_awaited()


async def test_an_unregistered_commenter_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, prepared: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    register_github_logins(monkeypatch)

    with caplog.at_level("INFO"):
        response = await _post(_event("issue_comment.created", _comment_payload()))

    assert response.json() is None
    prepared.assert_not_awaited()
    # One line says why the channel was told nothing starts.
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "Sender is not a registered Open SWE user"
    assert record.endpoint == "github"


async def test_the_public_repository_gate_applies(
    monkeypatch: pytest.MonkeyPatch, prepared: AsyncMock
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "enforce_public_repo_org_gate",
        AsyncMock(return_value={"status": "ignored", "reason": "not an org member"}),
    )

    response = await _post(_event("issues.opened", _issue_payload()))

    assert response.json() is None
    prepared.assert_not_awaited()


async def test_a_waiting_thread_is_only_asked_whether_open_swe_acts(prepared: AsyncMock) -> None:
    response = await _post(_event("issue_comment.created", _comment_payload(), interrupted=True))

    # Open SWE would act, so the channel points the issue to the decision; nothing starts.
    assert response.json() == {"input": "Open SWE was mentioned while the thread waits."}
    # No reaction, metadata or run token while the thread waits.
    prepared.assert_not_awaited()
    channel.adopt_channel_run.assert_not_awaited()  # type: ignore[attr-defined]


async def test_the_payload_must_be_the_events(prepared: AsyncMock) -> None:
    response = await _post(_event("issues.reopened", _issue_payload()))

    assert response.status_code == 422
    prepared.assert_not_awaited()
