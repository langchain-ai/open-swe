"""MDA's Linear channel calls the backend's typed channel API, and only it can."""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from runtime_api.linear import (
    LINEAR_AUTOMATION_RULES,
    LINEAR_SESSION_FACTS,
    PLAN_LINEAR_AUTOMATIONS,
    PLAN_LINEAR_SESSION,
    RECORD_LINEAR_DELIVERY,
    LinearAutomationRules,
    LinearAutomationRun,
    LinearSessionDeclined,
    LinearSessionFacts,
    LinearSessionRun,
    LinearSessionStart,
    PlanLinearAutomation,
    PlannedLinearAutomations,
    RepositoryChoice,
)
from standardwebhooks import Webhook

from openswe import dispatch
from openswe.agent_api.connections import ACTING_USER_KEY
from openswe.agent_api.tokens import verify_runtime_token
from openswe.linear import session
from openswe.mda_channels import api
from openswe.schedules import store
from openswe.users import User, UserIdentity
from tests.mda_helpers import mda_starts, stub_mda_runtime

_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value
_THREAD = str(uuid.uuid4())
_ADA = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.linear.channel.EventLog.record_once", record)
    app = FastAPI()
    app.state.record = record
    app.include_router(api.router)
    return app


def _delivery(key: str, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"key": key, "delivery_id": "delivery-1", "payload": payload}


def _facts_request() -> dict[str, JsonValue]:
    return {
        "delivery": _delivery("AgentSessionEvent.created", {"type": "AgentSessionEvent"}),
        "thread_id": _THREAD,
        "author_email": "ada@example.com",
    }


def _turn(body: str = "@Open SWE fix it") -> dict[str, JsonValue]:
    return {
        "thread_id": _THREAD,
        "session_id": "session-1",
        "issue": {"id": "issue-1", "identifier": "ENG-1", "title": "Bug"},
        "author": {"id": "user-1", "name": "Ada", "email": "ada@example.com"},
        "body": body,
        "comment_id": "comment-1",
    }


async def _post(
    app: FastAPI, path: str, body: dict[str, JsonValue], secret: str | None = _SERVICE_SECRET
) -> httpx.Response:
    """POST ``body`` as MDA does: signed with ``secret``, or unsigned without one."""
    content = json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if secret is not None:
        timestamp = datetime.now(tz=UTC)
        headers |= {
            "webhook-id": "msg_delivery-1",
            "webhook-timestamp": str(int(timestamp.timestamp())),
            "webhook-signature": Webhook(secret).sign(
                "msg_delivery-1", timestamp, content.decode()
            ),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, content=content, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, "ab" * 32])
async def test_only_the_mda_deployment_may_call(app: FastAPI, secret: str | None) -> None:
    body = _delivery("Issue.create", {"type": "Issue"})
    response = await _post(app, RECORD_LINEAR_DELIVERY.path, body, secret)
    assert response.status_code == 401
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_nobody_may_call_without_a_configured_secret(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MDA_SERVICE_SECRET")
    body = _delivery("Issue.create", {"type": "Issue"})
    assert (await _post(app, RECORD_LINEAR_DELIVERY.path, body)).status_code == 401


@pytest.mark.asyncio
async def test_unknown_calls_events_and_oversized_payloads_are_refused(app: FastAPI) -> None:
    body = _delivery("Issue.create", {"type": "Issue"})
    assert (await _post(app, "/mda/channel-api/drop_tables", body)).status_code == 404
    unknown = _delivery("Issue.remove", {})
    assert (await _post(app, RECORD_LINEAR_DELIVERY.path, unknown)).status_code == 422
    huge = _delivery("Issue.create", {"type": "Issue", "data": {"description": "x" * 1_000_001}})
    assert (await _post(app, RECORD_LINEAR_DELIVERY.path, huge)).status_code == 422
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliveries_are_logged(app: FastAPI) -> None:
    payload: dict[str, JsonValue] = {
        "type": "Issue",
        "action": "create",
        "actor": {"email": "ada@example.com"},
    }
    response = await _post(app, RECORD_LINEAR_DELIVERY.path, _delivery("Issue.create", payload))
    assert response.status_code == 200
    _request, body, source = app.state.record.await_args.args
    kwargs = app.state.record.await_args.kwargs
    assert (json.loads(body), source) == (payload, "linear")
    assert (kwargs["event_type"], kwargs["delivery_id"]) == ("Issue", "delivery-1")
    assert kwargs["refs"].email == "ada@example.com"


def _signed_in(monkeypatch: pytest.MonkeyPatch, *, known: bool = True, valid: bool = True) -> None:
    monkeypatch.setattr(session.User, "for_email", AsyncMock(return_value=_ADA if known else None))
    monkeypatch.setattr(
        session.common, "get_valid_access_token", AsyncMock(return_value="gho" if valid else None)
    )
    monkeypatch.setattr(session.common, "has_access_token_record", AsyncMock(return_value=True))
    monkeypatch.setattr(
        session, "build_settings_url", lambda: "https://swe.example/my-settings/connections"
    )


@pytest.mark.asyncio
async def test_a_session_turns_facts_are_what_open_swe_knows(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signed_in(monkeypatch)
    monkeypatch.setattr(
        session.common.ThreadRecord,
        "read",
        AsyncMock(
            return_value=session.common.ThreadRecord(
                True, {"workspace": "core", "repo": {"owner": "acme", "name": "api"}}
            )
        ),
    )
    personal = AsyncMock(side_effect=[{"owner": "ada", "name": "personal"}])
    monkeypatch.setattr(User, "default_repo", personal)

    body = _facts_request()
    response = await _post(app, LINEAR_SESSION_FACTS.path, body)

    facts = LinearSessionFacts.model_validate(response.json())
    assert facts.author == "signed_in"
    # The facts call logs the turn's delivery, so a turn takes two calls, not three.
    app.state.record.assert_awaited_once()
    assert app.state.record.await_args.kwargs["delivery_id"] == "delivery-1"
    assert (facts.thread_repository, facts.thread_workspace) == ("acme/api", "core")
    # The author's default for the thread's workspace.
    assert facts.personal_default == RepositoryChoice(repository="ada/personal", workspace="core")


@pytest.mark.asyncio
async def test_a_session_thread_it_cant_read_fails_the_turn(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never read as a new thread, which would move the session's repository."""
    _signed_in(monkeypatch)
    monkeypatch.setattr(
        session.common.ThreadRecord, "read", AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    )

    body = _facts_request()
    with pytest.raises(httpx.ReadTimeout):
        await _post(app, LINEAR_SESSION_FACTS.path, body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("known", "standing"),
    [(False, "sign_in"), (True, "sign_in_again")],
    ids=["unknown-author", "revoked-authorization"],
)
async def test_an_author_who_cant_run_as_themselves_learns_nothing_more(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, known: bool, standing: str
) -> None:
    _signed_in(monkeypatch, known=known, valid=False)
    thread = AsyncMock()
    monkeypatch.setattr(session.common.ThreadRecord, "read", thread)

    body = _facts_request()
    facts = LinearSessionFacts.model_validate(
        (await _post(app, LINEAR_SESSION_FACTS.path, body)).json()
    )

    assert (facts.author, facts.settings_url) == (
        standing,
        "https://swe.example/my-settings/connections",
    )
    # Nothing about the thread or the author's repositories.
    assert (facts.thread_repository, facts.personal_default) == (None, None)
    thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_session_turn_gets_the_run_context_mda_starts_it_with(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    _signed_in(monkeypatch)
    monkeypatch.setattr(session.common, "is_repo_allowed", lambda _repo: True)
    monkeypatch.setattr(session.common, "upsert_agent_thread_metadata", AsyncMock())
    monkeypatch.setattr(session, "adopt_channel_run", AsyncMock())
    monkeypatch.setattr(session, "channel_run_starting", AsyncMock())
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
    stub_mda_runtime(monkeypatch)

    plan = {"turn": _turn(), "repository": "acme/api", "workspace": "core"}
    response = await _post(app, PLAN_LINEAR_SESSION.path, plan)

    assert response.status_code == 200
    run = LinearSessionStart.model_validate(response.json()).answer
    assert isinstance(run, LinearSessionRun)
    assert (run.repository, run.title) == ("acme/api", "Bug")
    # The attribution the run's hooks read rides in its token.
    token = verify_runtime_token(str(run.context["__run_token"]))
    assert token.thread_id == _THREAD
    assert token.metadata["ls_attribution_identifier"] == "ada"
    assert token.configurable["workspace"] == "core"
    # MDA starts a channel's run as its sender, so its catalog may name their connections.
    assert token.metadata[ACTING_USER_KEY] == "user-1"
    # MDA starts the run: its metadata sets none of the keys MDA keeps.
    started = mda_starts({"input": "x", "context": run.context, "metadata": run.metadata})
    assert started["metadata"]["channel"] == "linear"
    reserved = {"user_id", "ls_repo_provider", "ls_attribution_identifier", ACTING_USER_KEY}
    assert not reserved & started["metadata"].keys()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signed_in", "allowed", "declined"),
    [
        (
            False,
            True,
            LinearSessionDeclined(
                reason="sign_in_again", settings_url="https://swe.example/my-settings/connections"
            ),
        ),
        (
            True,
            False,
            LinearSessionDeclined(reason="repository_not_allowed", repository="acme/api"),
        ),
    ],
    ids=["author-signed-out", "repository-not-allowed"],
)
async def test_the_backend_checks_the_channels_choice_before_it_signs_a_run(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    signed_in: bool,
    allowed: bool,
    declined: LinearSessionDeclined,
) -> None:
    _signed_in(monkeypatch, valid=signed_in)
    monkeypatch.setattr(session.common, "is_repo_allowed", lambda _repo: allowed)
    adopt = AsyncMock()
    monkeypatch.setattr(session, "adopt_channel_run", adopt)

    plan = {"turn": _turn(), "repository": "acme/api"}
    response = await _post(app, PLAN_LINEAR_SESSION.path, plan)

    assert LinearSessionStart.model_validate(response.json()).answer == declined
    # Refused before the thread is adopted or a run signed.
    adopt.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_teams_automation_rules_call_logs_the_issue_event(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "linear_automation_rules", AsyncMock(return_value=[]))
    body = {"delivery": _delivery("Issue.create", {"type": "Issue"}), "team": "ENG"}

    response = await _post(app, LINEAR_AUTOMATION_RULES.path, body)

    assert LinearAutomationRules.model_validate(response.json()).automations == []
    app.state.record.assert_awaited_once()
    assert app.state.record.await_args.kwargs["delivery_id"] == "delivery-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("login", ["ada", None], ids=["open-swe-user", "stranger"])
async def test_a_teams_automation_rules_say_whether_the_issues_creator_is_a_user(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, login: str | None
) -> None:
    record = {
        "id": "a1",
        "prompt": "Triage the bug",
        "triggers": [{"kind": "linear", "config": {"team": "ENG", "events": ["issue.created"]}}],
    }
    monkeypatch.setattr(store, "linear_automation_rules", AsyncMock(return_value=[record]))
    looked_up = AsyncMock(return_value=login)
    monkeypatch.setattr(User, "login_for_email", looked_up)
    issue: dict[str, JsonValue] = {"id": "issue-1", "creator": {"email": "ada@example.com"}}
    body = {
        "delivery": _delivery("Issue.create", {"type": "Issue", "data": issue}),
        "team": "ENG",
    }

    response = await _post(app, LINEAR_AUTOMATION_RULES.path, body)

    rules = LinearAutomationRules.model_validate(response.json())
    # The channel writes the run's prompt: the automation's own, and the issue, fenced
    # unless its creator is an Open SWE user.
    assert [(rule.id, rule.prompt) for rule in rules.automations] == [("a1", "Triage the bug")]
    assert rules.creator_registered is (login is not None)
    looked_up.assert_awaited_once_with("ada@example.com")


@pytest.mark.asyncio
async def test_every_automation_an_issue_event_fires_is_planned_in_one_call(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = LinearAutomationRun(
        thread_id=uuid.uuid4(), input={"messages": []}, context={}, metadata={}
    )

    async def plan(each: PlanLinearAutomation) -> LinearAutomationRun | None:
        # The second automation's claim is already taken for this delivery.
        return run if each.automation_id == "a1" else None

    planned = AsyncMock(side_effect=plan)
    monkeypatch.setattr(store, "plan_linear_automation", planned)
    body: dict[str, JsonValue] = {
        "delivery_id": "delivery-1",
        "automations": [
            {"automation_id": "a1", "event": "issue.created", "prompt": "Triage it"},
            {"automation_id": "a2", "event": "issue.labeled", "prompt": "Fix it"},
        ],
        "team": "ENG",
    }

    response = await _post(app, PLAN_LINEAR_AUTOMATIONS.path, body)

    # One run per automation, in order: none where the backend starts nothing.
    assert PlannedLinearAutomations.model_validate(response.json()).runs == [run, None]
    each = [call.args[0] for call in planned.await_args_list]
    assert [(p.automation_id, p.event, p.delivery_id, p.team, p.prompt) for p in each] == [
        ("a1", "issue.created", "delivery-1", "ENG", "Triage it"),
        ("a2", "issue.labeled", "delivery-1", "ENG", "Fix it"),
    ]
