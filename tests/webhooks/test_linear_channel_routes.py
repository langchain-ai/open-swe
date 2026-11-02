"""MDA's Linear channel asks the backend what each Linear event starts, and only it can."""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from standardwebhooks import Webhook

from openswe import dispatch
from openswe.agent_api.connections import ACTING_USER_KEY
from openswe.agent_api.tokens import verify_runtime_token
from openswe.linear import channel, session
from openswe.linear.session import NO_REPOSITORY, UNREADABLE_SESSION
from openswe.users import User, UserIdentity
from tests.mda_helpers import mda_declines, mda_starts, stub_mda_runtime

_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value
_THREAD = str(uuid.uuid4())


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.linear.channel.EventLog.record_once", record)
    automations = AsyncMock()
    monkeypatch.setattr(channel, "_launch_automations", automations)
    api = FastAPI()
    api.state.record = record
    api.state.automations = automations
    api.include_router(channel.router)
    return api


def _call(
    key: str, payload: dict[str, JsonValue], *, interrupted: bool = False
) -> dict[str, JsonValue]:
    """What MDA's channel POSTs its endpoint."""
    thread: dict[str, JsonValue] = {"id": _THREAD, "channel": {"name": "linear"}}
    if interrupted:
        thread["interrupted"] = True
    return {
        "event": {"key": key, "deliveryId": "delivery-1", "payload": payload},
        "thread": thread,
    }


async def _post(
    app: FastAPI, body: dict[str, JsonValue], secret: str | None = _SERVICE_SECRET
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
        return await client.post("/linear/channel/events", content=content, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, "ab" * 32])
async def test_only_the_mda_deployment_may_call(app: FastAPI, secret: str | None) -> None:
    response = await _post(app, _call("Issue.create", {"type": "Issue"}), secret)
    assert response.status_code == 401
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_nobody_may_call_without_a_configured_secret(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MDA_SERVICE_SECRET")
    response = await _post(app, _call("Issue.create", {"type": "Issue"}))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_events_and_oversized_payloads_are_refused(app: FastAPI) -> None:
    assert (await _post(app, _call("Issue.remove", {}))).status_code == 422
    huge = _call("Issue.create", {"type": "Issue", "data": {"description": "x" * 1_000_001}})
    assert (await _post(app, huge)).status_code == 422
    not_an_object = _call("Issue.create", {})
    not_an_object["event"]["payload"] = ["Issue"]
    assert (await _post(app, not_an_object)).status_code == 422
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_events_start_nothing_and_are_logged_and_run_automations(
    app: FastAPI,
) -> None:
    payload = {"type": "Issue", "action": "create", "actor": {"email": "ada@example.com"}}
    response = await _post(app, _call("Issue.create", payload))
    assert response.status_code == 200
    # The channel starts nothing for null.
    assert response.json() is None
    _request, body, source = app.state.record.await_args.args
    kwargs = app.state.record.await_args.kwargs
    assert (json.loads(body), source) == (payload, "linear")
    assert (kwargs["event_type"], kwargs["delivery_id"]) == ("Issue", "delivery-1")
    assert kwargs["refs"].email == "ada@example.com"
    automation_payload, delivery_id = app.state.automations.await_args.args
    assert (automation_payload, delivery_id) == (payload, "delivery-1")


@pytest.mark.asyncio
async def test_comments_are_logged_without_automations(app: FastAPI) -> None:
    response = await _post(app, _call("Comment.create", {"type": "Comment"}))
    assert response.json() is None
    app.state.record.assert_awaited_once()
    app.state.automations.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_session_turn_starts_the_run_prepared_on_the_sessions_thread(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = {
        "input": {"messages": []},
        "context": {"graph": "agent"},
        "metadata": {"channel": "linear"},
        "webhook": "https://backend.example/webhooks/run-complete?token=t",
    }
    prepare = AsyncMock(return_value=prepared)
    monkeypatch.setattr(channel.LinearSessionTurn, "prepare_run", prepare)
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {
            "id": "session-1",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "title": "Bug"},
            "comment": {"id": "comment-1", "body": "@Open SWE fix it"},
        },
    }

    response = await _post(app, _call("AgentSessionEvent.created", payload))

    assert response.status_code == 200
    # MDA's channel starts exactly this run, which completes through the webhook.
    assert response.json() == prepared
    (thread_id,) = prepare.await_args.args
    assert thread_id == _THREAD
    app.state.record.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_session_turn_on_an_interrupted_thread_prepares_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare = AsyncMock()
    monkeypatch.setattr(channel.LinearSessionTurn, "prepare_run", prepare)
    payload = {
        "type": "AgentSessionEvent",
        "action": "prompted",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {"id": "session-1", "issue": {"id": "issue-1"}},
        "agentActivity": {"content": {"type": "prompt", "body": "and the docs"}},
    }

    response = await _post(app, _call("AgentSessionEvent.prompted", payload, interrupted=True))

    # Only tells the channel Open SWE would act: no thread adopted, no run token signed.
    assert response.json() == {"input": channel._ACTS_WHILE_INTERRUPTED}
    prepare.assert_not_awaited()


@pytest.mark.asyncio
async def test_mda_starts_the_run_a_session_turn_prepares(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    monkeypatch.setattr(
        session.LinearSessionTurn,
        "repository",
        AsyncMock(return_value=({"owner": "acme", "name": "api"}, None)),
    )
    ada = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])
    monkeypatch.setattr(session.User, "for_email", AsyncMock(return_value=ada))
    monkeypatch.setattr(session.common, "get_valid_access_token", AsyncMock(return_value="gho"))
    monkeypatch.setattr(
        session.common.ThreadRecord,
        "read",
        AsyncMock(return_value=session.common.ThreadRecord(True, {"workspace": "acme"})),
    )
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
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {
            "id": "session-1",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "title": "Bug"},
            "comment": {"id": "comment-1", "body": "@Open SWE fix it"},
            "creator": {"id": "user-1", "name": "Ada", "email": "ada@example.com"},
        },
    }

    response = await _post(app, _call("AgentSessionEvent.created", payload))

    assert response.status_code == 200
    answer = response.json()
    # The attribution the run's hooks read rides in its token.
    token = verify_runtime_token(answer["context"]["__run_token"])
    assert token.thread_id == _THREAD
    assert token.metadata["ls_attribution_identifier"] == "ada"
    # MDA starts a channel's run as its sender, so its catalog may name their connections.
    assert token.metadata[ACTING_USER_KEY] == "user-1"
    # MDA starts the run: the answer sets none of the metadata keys MDA keeps.
    metadata = mda_starts(answer)["metadata"]
    assert metadata == answer["metadata"]
    assert metadata["channel"] == "linear"
    assert "invocation_id" in metadata
    reserved = {"user_id", "ls_repo_provider", "ls_attribution_identifier", ACTING_USER_KEY}
    assert not reserved & metadata.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signed_in", "decline"),
    [
        (None, "using the same email as your Linear account"),
        (False, "Your GitHub sign-in to Open SWE is no longer valid"),
    ],
    ids=["unknown-author", "revoked-authorization"],
)
async def test_a_session_turn_from_someone_who_cant_run_as_themselves_asks_them_to_sign_in(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, signed_in: bool | None, decline: str
) -> None:
    ada = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])
    monkeypatch.setattr(
        session.User, "for_email", AsyncMock(return_value=None if signed_in is None else ada)
    )
    monkeypatch.setattr(session.common, "get_valid_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(session.common, "has_access_token_record", AsyncMock(return_value=True))
    monkeypatch.setattr(
        session, "build_settings_url", lambda: "https://swe.example/my-settings/connections"
    )
    thread = AsyncMock()
    monkeypatch.setattr(session.common.ThreadRecord, "read", thread)
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {
            "id": "session-1",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "title": "Bug"},
            "comment": {"id": "comment-1", "body": "@Open SWE fix it in repo:acme/api"},
            "creator": {"id": "user-1", "name": "Ada", "email": "ada@example.com"},
        },
    }

    response = await _post(app, _call("AgentSessionEvent.created", payload))

    text = mda_declines(response.json())
    assert decline in text
    assert "(https://swe.example/my-settings/connections)" in text
    # Declined before the thread is read or adopted.
    thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_session_turn_the_backend_declines_tells_the_session_why(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        channel.LinearSessionTurn, "prepare_run", AsyncMock(return_value=NO_REPOSITORY)
    )
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {"id": "session-1", "issue": {"id": "issue-1"}},
    }
    response = await _post(app, _call("AgentSessionEvent.created", payload))
    # MDA's channel starts nothing, and posts the text in the session.
    assert response.json() == NO_REPOSITORY
    assert mda_declines(response.json()) == NO_REPOSITORY["decline"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_session",
    [{"id": "session-1"}, {"id": "session-1", "issue": None}],
    ids=["no-schema-match", "no-issue"],
)
async def test_a_session_it_cant_read_is_told_so(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, agent_session: dict[str, JsonValue]
) -> None:
    prepare = AsyncMock()
    monkeypatch.setattr(channel.LinearSessionTurn, "prepare_run", prepare)
    payload: dict[str, JsonValue] = {
        "type": "AgentSessionEvent",
        "action": "created",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": agent_session,
    }
    if "issue" not in agent_session:
        # Not a session event MDA's schema reads.
        del payload["webhookTimestamp"]
    response = await _post(app, _call("AgentSessionEvent.created", payload))
    assert response.json() == UNREADABLE_SESSION
    assert mda_declines(response.json()) == UNREADABLE_SESSION["decline"]
    prepare.assert_not_awaited()


@pytest.mark.parametrize(
    ("activity", "author_id", "author_email"),
    [
        ({"userId": "user-2"}, "user-2", ""),
        ({"userId": "user-1"}, "user-1", "ada@example.com"),
        ({}, "user-1", "ada@example.com"),
    ],
    ids=["someone-else-by-id", "the-creator-by-id", "no-one-named"],
)
def test_a_reply_named_only_by_id_never_borrows_the_creators_identity(
    activity: dict[str, JsonValue], author_id: str, author_email: str
) -> None:
    payload = {
        "type": "AgentSessionEvent",
        "action": "prompted",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {
            "id": "session-1",
            "issue": {"id": "issue-1"},
            "creator": {"id": "user-1", "name": "Ada", "email": "ada@example.com"},
        },
        "agentActivity": {"content": {"type": "prompt", "body": "and the docs"}, **activity},
    }

    turn = session.LinearSessionTurn(payload, {"id": "issue-1"})  # type: ignore[arg-type]

    # Without an email, someone other than the creator is asked to sign in.
    assert (turn.author.id, turn.author.email) == (author_id, author_email)


@pytest.mark.asyncio
async def test_a_session_thread_it_cant_read_fails_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never resolved as a new thread, which would move the session's repository."""
    ada = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])
    turn = session.LinearSessionTurn(
        {  # type: ignore[arg-type]
            "type": "AgentSessionEvent",
            "action": "created",
            "organizationId": "org-1",
            "webhookTimestamp": 1_700_000_000_000,
            "agentSession": {"id": "session-1", "issue": {"id": "issue-1"}},
        },
        {"id": "issue-1"},
    )
    monkeypatch.setattr(turn, "_signed_in_author", AsyncMock(return_value=ada))
    monkeypatch.setattr(
        session.common.ThreadRecord, "read", AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    )
    repository = AsyncMock()
    monkeypatch.setattr(turn, "repository", repository)

    with pytest.raises(httpx.ReadTimeout):
        await turn.prepare_run(_THREAD)
    repository.assert_not_awaited()
