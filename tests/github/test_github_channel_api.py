"""MDA's GitHub channel calls the backend's typed channel API, and only it can."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from runtime_api.github import (
    ACT_ON_GITHUB_DELIVERY,
    GITHUB_AUTOMATION_RULES,
    GITHUB_DELIVERY_FACTS,
    GITHUB_ISSUE_FACTS,
    RECORD_GITHUB_DELIVERY,
    ActOnGitHubDelivery,
    GitHubAutomationRulesRequest,
    GitHubDelivery,
    GitHubIssueFactsRequest,
    GitHubIssueRunRequest,
)
from standardwebhooks import Webhook

from openswe import dispatch
from openswe.agent_api.tokens import verify_runtime_token
from openswe.github import channel
from openswe.github import webhook as github_webhooks
from openswe.mda_channels import api, github_deliveries
from openswe.users import User
from openswe.webhooks import common as webhook_common
from tests.conftest import register_github_logins
from tests.github_channel import (
    ISSUE_RUN_INPUT,
    SERVICE_SECRET,
    GitHubDelivered,
    call_as_mda,
    deliver_as_mda,
    forward_as_mda,
)
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
    monkeypatch.setattr(channel.schedules, "github_automation_rules", AsyncMock(return_value=[]))
    monkeypatch.setattr(github_webhooks, "conversation_started", AsyncMock(return_value=False))
    monkeypatch.setattr(webhook_common, "get_thread_workspace", AsyncMock(return_value="team-a"))
    monkeypatch.setattr(User, "email_for_login", AsyncMock(return_value="ada@example.com"))
    monkeypatch.setattr(
        webhook_common, "enforce_public_repo_org_gate", AsyncMock(return_value=None)
    )
    register_github_logins(monkeypatch, "ada")
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(github_deliveries.router)
    return app


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The issue run's configurable, and the thread the channel adopts."""
    run = {"source": "github", "github_login": "ada", "repo": {"owner": "acme"}}

    async def adopt_then_prepare(
        *_args: object,
        workspace: str | None,
        adopt: Callable[[str], Awaitable[None]] | None = None,
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


async def test_a_forwarded_delivery_runs_the_backends_work_once(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    process = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_ci_event", process)
    delivery = GitHubDelivery(
        event="status",
        delivery_id="delivery-1",
        payload={"repository": {"name": "api", "owner": {"login": "acme"}}},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # GitHub delivers it again after a failure MDA answered it with.
        for _ in range(2):
            response = await forward_as_mda(client, delivery, SERVICE_SECRET)
            assert response.status_code == 202

    process.assert_awaited_once()


async def test_a_forward_answers_before_picking_the_work_and_a_failed_lookup_keeps_it(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    """MDA answers GitHub only once the forward is answered, so the backend answers
    before it looks anything up. A lookup that fails runs nothing and leaves the work
    unclaimed, so the delivery, redelivered, runs it."""
    process = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_ci_event", process)
    routable = AsyncMock(side_effect=RuntimeError("the database is away"))
    monkeypatch.setattr(channel, "repo_is_routable", routable)
    delivery = GitHubDelivery(
        event="status",
        delivery_id="delivery-1",
        payload={"repository": {"name": "api", "owner": {"login": "acme"}}},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await forward_as_mda(client, delivery, SERVICE_SECRET)
        assert response.status_code == 202
        process.assert_not_awaited()
        channel.EventLog.record_once.assert_awaited_once()  # type: ignore[attr-defined]

        routable.side_effect = None
        routable.return_value = True
        await forward_as_mda(client, delivery, SERVICE_SECRET)

    process.assert_awaited_once()


async def test_the_facts_log_and_run_nothing(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    process = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_ci_event", process)
    delivery = GitHubDelivery(
        event="status",
        delivery_id="delivery-1",
        payload={"repository": {"name": "api", "owner": {"login": "acme"}}},
    )

    facts = await call_as_mda(app, GITHUB_DELIVERY_FACTS, delivery, secret=SERVICE_SECRET)

    assert facts.allowed and facts.routable
    process.assert_not_awaited()
    channel.EventLog.record_once.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"x-github-delivery": "delivery-1"}, 400),
    ],
)
async def test_a_forward_mda_didnt_sign_or_that_names_no_event_is_refused(
    app: FastAPI, headers: dict[str, str], status: int
) -> None:
    content = '{"action": "opened"}'
    if status != 401:
        timestamp = datetime.now(tz=UTC)
        headers = {
            **headers,
            "webhook-id": "msg_delivery-1_forward",
            "webhook-timestamp": str(int(timestamp.timestamp())),
            "webhook-signature": Webhook(SERVICE_SECRET).sign(
                "msg_delivery-1_forward", timestamp, content
            ),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/mda/github/deliveries", content=content, headers=headers)

    assert response.status_code == status


async def test_the_backend_runs_its_work_when_it_records_the_delivery(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    process = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_ci_event", process)
    delivery = GitHubDelivery(
        event="status",
        delivery_id="delivery-1",
        payload={"repository": {"name": "api", "owner": {"login": "acme"}}},
    )

    await call_as_mda(app, RECORD_GITHUB_DELIVERY, delivery, secret=SERVICE_SECRET)
    process.assert_awaited_once()
    # A channel deployed before the backend picked its own work still names it; it
    # doesn't run twice.
    await call_as_mda(
        app,
        ACT_ON_GITHUB_DELIVERY,
        ActOnGitHubDelivery(**delivery.model_dump(), work=["ci_event"]),
        secret=SERVICE_SECRET,
    )
    process.assert_awaited_once()


async def test_automations_that_fail_to_plan_start_nothing_and_stop_nothing(
    app: FastAPI, prepared: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automations used to run in the background, where one that failed stopped
    nothing else; planning them here keeps that."""
    monkeypatch.setattr(
        channel.schedules,
        "plan_github_automations",
        AsyncMock(side_effect=RuntimeError("the automations could not be planned")),
    )
    act = ActOnGitHubDelivery(
        event="issues",
        delivery_id="delivery-1",
        payload=_issue_payload(),
        automations=[
            {
                "automation_id": "a-0",
                "event": "issues.opened",
                "prompt": "Triage it",
            }
        ],
        issue_run=GitHubIssueRunRequest(thread_id=THREAD, input=ISSUE_RUN_INPUT),
    )

    acted = await call_as_mda(app, ACT_ON_GITHUB_DELIVERY, act, secret=SERVICE_SECRET)

    assert acted.automations == []
    # The mention's run is still planned.
    assert acted.issue_run is not None and acted.issue_run.starts
    prepared.assert_awaited_once()


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
    forget_installations = AsyncMock()
    monkeypatch.setattr(channel.REPO_INSTALLATIONS, "forget_all", forget_installations)
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
    forget_installations.assert_awaited_once()


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
    prepared.assert_awaited_once()
    assert prepared.await_args is not None
    assert prepared.await_args.args[1:] == (str(THREAD),)
    # A new conversation: the repository's own workspace.
    assert prepared.await_args.kwargs["workspace"] is None
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
    metadata = mda_starts({"input": ISSUE_RUN_INPUT, **run.model_dump()})["metadata"]
    assert metadata == run.metadata
    assert metadata["channel"] == "github"
    assert metadata["github_delivery"] == "delivery-1"
    assert metadata["LANGSMITH_AGENT_VERSION"] == "v1"
    assert "invocation_id" in metadata
    assert not {"user_id", "ls_repo_provider", "ls_attribution_identifier"} & metadata.keys()


async def test_a_pr_comment_starts_on_the_pull_requests_own_thread(
    app: FastAPI, prepared: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    pr_thread = str(uuid.uuid4())

    async def comment(
        _payload: object, _event: str, *, agent_thread_id: str | None, channel: object
    ) -> None:
        # The real one adopts the PR's own thread, then hands the run to the channel.
        assert agent_thread_id is None
        await channel.adopt(pr_thread, {"branch_name": "fix"})  # type: ignore[attr-defined]
        await channel.start(  # type: ignore[attr-defined]
            pr_thread,
            {"source": "github", "github_login": "ada", "pr_number": 7},
            {"messages": [{"role": "user", "content": "Fix CI"}]},
        )

    monkeypatch.setattr(github_webhooks, "process_github_pr_comment", comment)

    delivered = await _deliver(app, "issue_comment", _comment_payload(pull_request={"url": "u"}))

    assert delivered.comment
    run = delivered.comment_run
    assert run is not None and str(run.thread_id) == pr_thread
    assert run.input == {"messages": [{"role": "user", "content": "Fix CI"}]}
    channel.adopt_channel_run.assert_awaited_once_with(  # type: ignore[attr-defined]
        pr_thread, "github", metadata={"branch_name": "fix"}
    )
    channel.channel_run_starting.assert_awaited_once_with(pr_thread)  # type: ignore[attr-defined]
    token = verify_runtime_token(str(run.context["__run_token"]))
    assert (token.thread_id, token.configurable["github_login"]) == (pr_thread, "ada")
    metadata = mda_starts(run.model_dump(exclude={"thread_id"}, exclude_none=True))["metadata"]
    assert metadata["github_delivery"] == "delivery-1"


async def test_a_pr_comment_the_backend_starts_itself_starts_once_per_delivery(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    started = AsyncMock()

    async def comment(
        _payload: object, _event: str, *, agent_thread_id: str | None, channel: object
    ) -> None:
        # The agent's own thread: the backend starts the run, once per delivery.
        if await channel.once():  # type: ignore[attr-defined]
            await started()

    monkeypatch.setattr(github_webhooks, "process_github_pr_comment", comment)
    payload = _comment_payload(pull_request={"url": "u"})

    # MDA sends the call again after an answer it didn't get.
    for _ in range(2):
        delivered = await _deliver(app, "issue_comment", payload)
        assert delivered.comment_run is None

    started.assert_awaited_once()


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
    assert record.no_run_reason == "Sender is not a registered Robin user"
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


async def test_a_follow_up_stays_in_the_workspace_its_thread_started_in(
    app: FastAPI, prepared: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github_webhooks, "conversation_started", AsyncMock(return_value=True))

    delivered = await _deliver(app, "issue_comment", _comment_payload())

    assert delivered.issue_run is not None and delivered.issue_run.starts
    assert prepared.await_args is not None
    assert prepared.await_args.kwargs["workspace"] == "team-a"


async def test_an_issue_run_needs_the_input_the_channel_wrote() -> None:
    with pytest.raises(ValueError, match="needs its input"):
        GitHubIssueRunRequest(thread_id=THREAD)


@pytest.mark.parametrize("started", [True, False])
async def test_issue_facts_say_who_is_a_user_and_what_the_thread_holds(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, started: bool
) -> None:
    monkeypatch.setattr(github_webhooks, "conversation_started", AsyncMock(return_value=started))
    register_github_logins(monkeypatch, "ada", "Carol")

    facts = await call_as_mda(
        app,
        GITHUB_ISSUE_FACTS,
        GitHubIssueFactsRequest(
            thread_id=THREAD, sender="ada", logins=["ada", "bob", "carol", "x[bot]"]
        ),
        secret=SERVICE_SECRET,
    )

    assert facts.trusted == ["ada", "carol"]
    assert (facts.conversation_started, facts.sender_email_known) == (started, True)
    # Only a follow-up reads the workspace its thread started in.
    assert facts.thread_workspace == ("team-a" if started else None)


async def test_issue_facts_refuse_a_login_github_cant_spell(app: FastAPI) -> None:
    with pytest.raises(ValueError, match="logins"):
        GitHubIssueFactsRequest(thread_id=THREAD, sender="ada", logins=["../etc"])
    with pytest.raises(httpx.HTTPStatusError):
        await call_as_mda(
            app,
            GITHUB_ISSUE_FACTS,
            GitHubIssueFactsRequest.model_construct(thread_id=THREAD, sender="a b", logins=[]),
            secret=SERVICE_SECRET,
        )


async def test_automation_rules_name_each_automations_events_on_the_repository(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {
        "id": "00000000-0000-4000-8000-0000000000a1",
        "prompt": "Triage it",
        "triggers": [
            {"kind": "github", "config": {"repo": "Acme/API", "events": ["issues.opened"]}},
            # Another repository's trigger configures its own events.
            {"kind": "github", "config": {"repo": "acme/web", "events": ["pull_request.opened"]}},
            {"kind": "linear", "config": {"team": "ENG", "events": ["issue.created"]}},
        ],
    }
    rules = AsyncMock(return_value=[record])
    monkeypatch.setattr(channel.schedules, "github_automation_rules", rules)
    register_github_logins(monkeypatch, "bob")

    answer = await call_as_mda(
        app,
        GITHUB_AUTOMATION_RULES,
        GitHubAutomationRulesRequest(repository="Acme/API", author="Bob"),
        secret=SERVICE_SECRET,
    )

    rules.assert_awaited_once_with("acme/api")
    assert answer.author_registered is True
    assert [(rule.id, rule.prompt, rule.events) for rule in answer.automations] == [
        (record["id"], "Triage it", ["issues.opened"])
    ]


@pytest.mark.parametrize("status", [404, 500])
async def test_an_issue_thread_mda_never_ran_has_not_started(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    request = httpx.Request("GET", "http://mda.test/threads/t1/state")
    error = httpx.HTTPStatusError(
        "MDA answered", request=request, response=httpx.Response(status, request=request)
    )
    monkeypatch.setattr(github_webhooks.THREADS, "state", AsyncMock(side_effect=error))

    if status == 404:
        assert await github_webhooks.conversation_started("t1") is False
    else:
        with pytest.raises(httpx.HTTPStatusError):
            await github_webhooks.conversation_started("t1")
