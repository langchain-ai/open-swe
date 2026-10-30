"""MDA's GitHub channel calls the backend's typed channel API, and only it can."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from runtime_api.github import ACT_ON_GITHUB_DELIVERY, RECORD_GITHUB_DELIVERY, ActOnGitHubDelivery
from standardwebhooks import Webhook

from openswe import dispatch
from openswe.agent_api.tokens import verify_runtime_token
from openswe.github import channel
from openswe.github import webhook as github_webhooks
from openswe.mda_channels import api
from openswe.webhooks import common as webhook_common
from tests.conftest import register_github_logins
from tests.github_channel import SERVICE_SECRET, GitHubDelivered, deliver_as_mda
from tests.mda_helpers import mda_starts, stub_mda_runtime

THREAD = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


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


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    monkeypatch.setattr("openswe.github.channel.EventLog.record_once", AsyncMock(return_value=True))
    monkeypatch.setattr(channel.Topic.PULL_REQUESTS, "invalidate", AsyncMock())
    monkeypatch.setattr(channel, "repo_is_routable", AsyncMock(return_value=True))
    monkeypatch.setattr(channel.schedules, "plan_github_automations", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        webhook_common, "enforce_public_repo_org_gate", AsyncMock(return_value=None)
    )
    register_github_logins(monkeypatch, "ada")
    app = FastAPI()
    app.include_router(api.router)
    return app


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The issue run ``process_github_issue`` would start, and the thread the channel adopts."""
    run = github_webhooks.GitHubIssueRun(
        thread_id=str(THREAD),
        input={"messages": [{"role": "user", "content": "Fix the crash"}]},
        configurable={"source": "github", "github_login": "ada", "repo": {"owner": "acme"}},
    )

    async def adopt_then_prepare(
        *_args: object, adopt: Callable[[str], Awaitable[None]] | None = None
    ) -> object:
        # The real one adopts the thread before it writes the thread's metadata.
        if adopt is not None and prepare.return_value is not None:
            await adopt(str(THREAD))
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


async def _deliver(
    app: FastAPI, event: str, payload: dict[str, JsonValue], **options: object
) -> GitHubDelivered:
    return await deliver_as_mda(
        app,
        event,
        payload,
        secret=SERVICE_SECRET,
        delivery_id="delivery-1",
        thread_id=THREAD,
        **options,  # type: ignore[arg-type]
    )


async def test_only_calls_mda_signed_are_answered(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            RECORD_GITHUB_DELIVERY.path,
            json={"event": "issues", "delivery_id": "delivery-1", "payload": _issue_payload()},
        )

    assert response.status_code == 401


async def test_a_call_that_starts_nothing_is_refused(app: FastAPI) -> None:
    content = ActOnGitHubDelivery.model_construct(
        event="issues", delivery_id="delivery-1", payload={}, work=[], issue_run=None
    ).model_dump_json()
    now = datetime.now(tz=UTC)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            ACT_ON_GITHUB_DELIVERY.path,
            content=content,
            headers={
                "content-type": "application/json",
                "webhook-id": "msg_1",
                "webhook-timestamp": str(int(now.timestamp())),
                "webhook-signature": Webhook(SERVICE_SECRET).sign("msg_1", now, content),
            },
        )

    assert response.status_code == 422


async def test_a_delivery_is_logged_with_what_open_swe_knows_of_its_repository(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhook_common, "is_repo_auto_review_enabled", AsyncMock(return_value=True))
    payload: dict[str, JsonValue] = {
        "action": "opened",
        "pull_request": {"number": 7},
        "repository": {"name": "api", "owner": {"login": "acme"}, "full_name": "acme/api"},
    }

    delivered = await _deliver(app, "pull_request", payload)

    assert delivered.facts.routable and delivered.facts.allowed and delivered.facts.auto_review
    assert "@openswe" in delivered.facts.tags
    channel.EventLog.record_once.assert_awaited_once()  # type: ignore[attr-defined]
    assert channel.EventLog.record_once.await_args.kwargs["delivery_id"] == "delivery-1"  # type: ignore[attr-defined]
    channel.Topic.PULL_REQUESTS.invalidate.assert_awaited_once()  # type: ignore[attr-defined]


async def test_automatic_review_is_only_read_for_pull_requests_and_pushes(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook_common, "is_repo_auto_review_enabled", enabled)

    delivered = await _deliver(app, "issues", _issue_payload(issue={"title": "x", "body": "y"}))

    assert delivered.facts.auto_review is False
    enabled.assert_not_awaited()


async def test_one_piece_of_work_failing_runs_the_rest(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        webhook_common, "is_repo_auto_review_enabled", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(channel, "wake_native_queue", AsyncMock(side_effect=RuntimeError("down")))
    settled = AsyncMock()
    monkeypatch.setattr(github_webhooks, "settle_human_reviews", settled)
    monkeypatch.setattr(webhook_common, "update_agent_thread_pr_state", AsyncMock())
    monkeypatch.setattr(webhook_common, "update_agent_pr_usage_from_webhook", AsyncMock())
    monkeypatch.setattr(github_webhooks, "process_github_pr_close", AsyncMock())
    payload: dict[str, JsonValue] = {
        "action": "reopened",
        "pull_request": {"number": 7},
        "repository": {"name": "api", "owner": {"login": "acme"}},
    }

    delivered = await _deliver(app, "pull_request", payload)

    assert delivered.work[0] == "wake_merge_queue"
    settled.assert_awaited_once()


async def test_work_runs_once_per_delivery(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    process = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_ci_event", process)
    payload: dict[str, JsonValue] = {"repository": {"name": "api", "owner": {"login": "acme"}}}

    # MDA sends the call again after an answer it didn't get.
    for _ in range(2):
        await _deliver(app, "status", payload)

    process.assert_awaited_once()


@pytest.mark.parametrize("action", ["added", "removed"])
async def test_an_installation_change_drops_the_cached_repository_listings(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    forget = AsyncMock()
    monkeypatch.setattr(channel, "forget_cached_repos", forget)
    payload: dict[str, JsonValue] = {
        "action": action,
        "installation": {"id": 1, "account": {"id": 3, "login": "acme"}},
        "repository_selection": "selected",
        "repositories_added": [],
        "repositories_removed": [],
        "sender": {"id": 2, "login": "ada", "type": "User"},
    }

    delivered = await _deliver(app, "installation_repositories", payload)

    assert delivered.work == ("forget_cached_repos",)
    forget.assert_awaited_once()


@pytest.mark.parametrize(
    ("event", "payload"),
    [("issues", _issue_payload()), ("issue_comment", _comment_payload())],
)
async def test_an_issue_mention_starts_a_run_on_the_channels_thread(
    app: FastAPI,
    prepared: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    payload: dict[str, JsonValue],
) -> None:
    webhook = "https://backend.example/webhooks/run-complete?token=t"
    monkeypatch.setattr(channel, "COMPLETION_WEBHOOK_URL", webhook)

    delivered = await _deliver(app, event, payload)

    assert delivered.issue_run is not None and delivered.issue_run.starts
    run = delivered.issue_run.run
    assert run is not None
    assert run.input == {"messages": [{"role": "user", "content": "Fix the crash"}]}
    prepared.assert_awaited_once()
    assert prepared.await_args is not None
    assert prepared.await_args.args[1:] == (event, str(THREAD))
    # The run completes through the backend's run-complete webhook.
    assert run.webhook == webhook
    channel.adopt_channel_run.assert_awaited_once_with(str(THREAD), "github")  # type: ignore[attr-defined]
    # Busy only once the run is ready, so a failure on the way never leaves it busy.
    channel.channel_run_starting.assert_awaited_once_with(str(THREAD))  # type: ignore[attr-defined]
    # The run token is signed for the channel's thread.
    context = run.context
    token = verify_runtime_token(str(context["__run_token"]))
    assert token.thread_id == str(THREAD)
    assert token.configurable["github_login"] == "ada"
    assert context["graph"] == "agent"
    # The attribution the run's hooks read rides in its token.
    assert token.metadata["ls_attribution_identifier"] == "ada"
    # MDA starts the run: the answer sets none of the metadata keys MDA keeps.
    metadata = mda_starts(run.model_dump())["metadata"]
    assert metadata == run.metadata
    assert metadata["channel"] == "github"
    assert metadata["github_delivery"] == "delivery-1"
    assert metadata["LANGSMITH_AGENT_VERSION"] == "v1"
    assert "invocation_id" in metadata
    assert not {"user_id", "ls_repo_provider", "ls_attribution_identifier"} & metadata.keys()


async def test_an_unregistered_commenter_starts_nothing(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    prepared: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    register_github_logins(monkeypatch)

    with caplog.at_level("INFO"):
        delivered = await _deliver(app, "issue_comment", _comment_payload())

    assert delivered.issue_run is not None and not delivered.issue_run.starts
    prepared.assert_not_awaited()
    # One line says why the channel was told nothing starts.
    [record] = [r for r in caplog.records if hasattr(r, "no_run_reason")]
    assert record.no_run_reason == "Sender is not a registered Open SWE user"
    assert record.endpoint == "github"


async def test_the_public_repository_gate_applies(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, prepared: AsyncMock
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "enforce_public_repo_org_gate",
        AsyncMock(return_value={"status": "ignored", "reason": "not an org member"}),
    )

    delivered = await _deliver(app, "issues", _issue_payload())

    assert delivered.issue_run is not None and not delivered.issue_run.starts
    prepared.assert_not_awaited()


async def test_a_waiting_thread_is_only_asked_whether_open_swe_acts(
    app: FastAPI, prepared: AsyncMock
) -> None:
    delivered = await _deliver(app, "issue_comment", _comment_payload(), waiting=True)

    # Open SWE would act, so the channel points the issue to the decision; nothing starts.
    assert delivered.issue_run is not None
    assert delivered.issue_run.starts and delivered.issue_run.run is None
    # No reaction, metadata or run token while the thread waits.
    prepared.assert_not_awaited()
    channel.adopt_channel_run.assert_not_awaited()  # type: ignore[attr-defined]
