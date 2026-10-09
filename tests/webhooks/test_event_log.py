import asyncio
import json
from datetime import UTC, date, datetime
from typing import Literal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text
from starlette.requests import Request
from starlette.types import Message

from openswe.database import transaction
from openswe.slack import routes
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackChannelContext
from openswe.webhooks import common, event_log
from openswe.webhooks.event_log import EventLog, EventRefs


async def test_posthog_webhook_excludes_raw_payload_and_keeps_unlinked_events(monkeypatch):
    from openswe.analytics import posthog
    from openswe.webhooks.event_log import LoggedEvent

    monkeypatch.setenv("POSTHOG_API_KEY", "test-key")
    monkeypatch.setenv("DD_ENV", "staging")
    requests: list[dict[str, object]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        posthog.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(respond)),
    )
    event = LoggedEvent(
        source="github",
        event_type="issue_comment",
        delivery_id="delivery-1",
        received_at=datetime.now(UTC),
        user_id=None,
        workspace_id=None,
        repository_id=None,
        pull_request_id=None,
        payload={"action": "created", "comment": {"body": "private content"}},
    )
    await posthog.record_webhook(event)
    assert requests[0]["properties"]["distinct_id"] == "open-swe:webhook:github"
    assert requests[0]["event"] == "Webhook Received"
    assert "private content" not in json.dumps(requests)
    assert requests[0]["properties"]["action"] == "created"
    assert requests[0]["properties"]["environment"] == "staging"
    user_id = uuid4()
    from openswe.users import User

    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=User(id=user_id)))
    await posthog.record_webhook(event.model_copy(update={"user_id": user_id}))
    for event_type in ("page", "track"):
        await posthog.record_usage(
            login="alice",
            email="alice@example.com",
            event_type=event_type,
            name="usage",
            properties={},
        )
    assert {request["properties"]["distinct_id"] for request in requests[1:]} == {str(user_id)}
    assert requests[2]["properties"]["$set"]["github_login"] == "alice"
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=None))
    await posthog.record_usage(
        login="unknown",
        email=None,
        event_type="page",
        name="usage",
        properties={},
    )
    assert len(requests) == 4
    monkeypatch.delenv("POSTHOG_API_KEY")
    await posthog.record_webhook(event)
    assert len(requests) == 4


async def _partitions() -> set[str]:
    async with transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'event_log'::regclass"
            )
        )
        return set(rows.scalars().all())


async def test_kinds_name_actions_and_narrow_by_prefix(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(event_log, "_ROTATED_AT", None)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/github",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }
    )
    since = datetime.now(UTC)
    for event_type, payload in (
        ("pull_request", {"action": "opened", "number": 1}),
        ("pull_request", {"action": "opened", "number": 2}),
        ("pull_request", {"action": "closed", "number": 1}),
        ("pull_request_review", {"action": "submitted"}),
        ("push", {"ref": "refs/heads/main"}),
    ):
        await EventLog.record(
            request, json.dumps(payload).encode(), "github", event_type=event_type
        )

    kinds = await EventLog.kinds(since)
    assert [(kind.event_type, kind.count) for kind in kinds] == [
        ("pull_request.closed", 1),
        ("pull_request.opened", 2),
        ("pull_request_review.submitted", 1),
        ("push", 1),
    ]
    narrowed = await EventLog.kinds(since, source="github", event_type="pull_request")
    assert [kind.event_type for kind in narrowed] == ["pull_request.closed", "pull_request.opened"]
    assert all(kind.payload_shape is None for kind in narrowed)
    (opened,) = await EventLog.kinds(since, source="github", event_type="pull_request.opened")
    assert opened.payload_shape == {"action": "string", "number": "number"}


async def test_rotation_keeps_yesterday_today_and_tomorrow(registry_db: None) -> None:
    await EventLog.rotate_partitions(date(2026, 9, 1))
    assert await _partitions() == {"event_log_20260901", "event_log_20260902"}

    await EventLog.rotate_partitions(date(2026, 9, 3))
    assert await _partitions() == {
        "event_log_20260902",
        "event_log_20260903",
        "event_log_20260904",
    }


async def test_record_creates_its_partition_and_stores_form_bodies_as_objects(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.analytics import posthog

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_export(event: event_log.LoggedEvent) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(posthog, "record_webhook", slow_export)
    monkeypatch.setattr(event_log, "_ROTATED_AT", None)
    monkeypatch.setattr(
        "openswe.slack.channels.SlackChannel.load",
        AsyncMock(
            return_value=SlackChannel.from_payload(
                {
                    "id": "CPUBLIC",
                    "is_channel": True,
                    "is_member": True,
                    "is_private": False,
                    "is_im": False,
                    "is_mpim": False,
                }
            )
        ),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/commands",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        }
    )

    try:
        await asyncio.wait_for(
            EventLog.record(
                request,
                b"command=%2Foswe&text=hello+there",
                "slack",
                event_type="/oswe",
                delivery_id="trigger-1",
                refs=EventRefs(slack_channel_id="CPUBLIC"),
            ),
            timeout=2,
        )
        await asyncio.wait_for(started.wait(), timeout=2)
    finally:
        release.set()
        await asyncio.gather(*event_log._POSTHOG_TASKS)

    async with transaction() as conn:
        row = (
            await conn.execute(
                text("SELECT source, endpoint, event_type, delivery_id, payload FROM event_log")
            )
        ).one()
    assert tuple(row) == (
        "slack",
        "/webhooks/slack/commands",
        "/oswe",
        "trigger-1",
        {"command": "/oswe", "text": "hello there"},
    )


async def test_record_links_a_github_pr_comment_to_its_rows(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(event_log, "_ROTATED_AT", None)
    ids = {name: uuid4() for name in ("user", "workspace", "repository", "pull_request")}
    async with transaction() as conn:
        for statement in (
            "INSERT INTO users (id) VALUES (:user)",
            "INSERT INTO user_identity (user_id, provider, external_id) "
            "VALUES (:user, 'github', '42')",
            "INSERT INTO workspace (id, slug, name) VALUES (:workspace, 'acme', 'Acme')",
            "INSERT INTO repository (id, key, full_name) "
            "VALUES (:repository, 'acme/widgets', 'acme/widgets')",
            "INSERT INTO workspace_repository (repository_id, workspace_id) "
            "VALUES (:repository, :workspace)",
            "INSERT INTO pull_request (id, repository_id, number, owner, repo) "
            "VALUES (:pull_request, :repository, 7, 'acme', 'widgets')",
        ):
            await conn.execute(text(statement), ids)
    body = json.dumps(
        {
            "repository": {"full_name": "Acme/Widgets"},
            "sender": {"id": 42},
            "issue": {"number": 7, "pull_request": {"url": "https://example.test/pulls/7"}},
        }
    ).encode()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/github",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }
    )

    await EventLog.record(request, body, "github", refs=EventRefs.github(body))

    async with transaction() as conn:
        row = (
            await conn.execute(
                text("SELECT user_id, workspace_id, repository_id, pull_request_id FROM event_log")
            )
        ).one()
    assert tuple(row) == (ids["user"], ids["workspace"], ids["repository"], ids["pull_request"])


@pytest.mark.parametrize("kind", ["message", "me_message", "edit", "multiple", "unknown"])
async def test_slack_event_links_a_single_known_pr_without_dispatching(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["message", "me_message", "edit", "multiple", "unknown"],
) -> None:
    monkeypatch.setattr(event_log, "_ROTATED_AT", None)
    monkeypatch.setattr(
        "openswe.slack.channels.SlackChannel.load",
        AsyncMock(
            return_value=SlackChannel.from_payload(
                {
                    "id": "CPUBLIC",
                    "is_channel": True,
                    "is_member": True,
                    "is_private": False,
                    "is_im": False,
                    "is_mpim": False,
                }
            )
        ),
    )
    monkeypatch.setattr(common, "verify_slack_signature", lambda **kwargs: True)
    monkeypatch.setattr(
        "openswe.incidents.channels.handle_slack_event", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(is_ext_shared=True)),
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(common, "resolve_slack_thread_id", dispatch)
    ids = {name: uuid4() for name in ("user", "repository", "pull_request")}
    async with transaction() as conn:
        for statement in (
            "INSERT INTO users (id) VALUES (:user)",
            "INSERT INTO user_identity (user_id, provider, external_id) VALUES (:user, 'slack', 'U1')",
            "INSERT INTO repository (id, key, full_name) VALUES (:repository, 'acme/widgets', 'acme/widgets')",
            "INSERT INTO pull_request (id, repository_id, number, owner, repo) VALUES (:pull_request, :repository, 7, 'acme', 'widgets')",
        ):
            await conn.execute(text(statement), ids)
    message = {
        "ts": "1786573369.551099",
        "thread_ts": "1786573300.000000",
        "user": "U1",
        "text": "<http://www.github.com/Acme/Widgets/pull/7/files|PR> https://github.com/acme/widgets/pull/7",
    }
    if kind == "multiple":
        message["text"] += " https://github.com/acme/widgets/pull/8"
    elif kind == "unknown":
        message["text"] = "https://github.com/acme/widgets/pull/99"
    event = (
        {
            "type": "message",
            "channel": "C1",
            "subtype": "message_changed",
            "message": {
                **message,
                "text": "",
                "blocks": [{"type": "section", "text": message["text"]}],
            },
            "previous_message": {**message, "text": "https://github.com/acme/widgets/pull/8"},
        }
        if kind == "edit"
        else {"type": "message", "channel": "C1", **message}
    )
    if kind == "me_message":
        event["subtype"] = "me_message"
    payload = {"type": "event_callback", "team_id": "T1", "event_id": "Ev1", "event": event}
    body = json.dumps(payload).encode()

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack",
            "query_string": b"",
            "headers": [],
        },
        receive,
    )
    tasks = BackgroundTasks()
    response = await routes.slack_webhook(request, tasks)
    await tasks()
    assert response["status"] == "ignored"
    dispatch.assert_not_awaited()
    async with transaction() as conn:
        row = (
            await conn.execute(
                text("SELECT user_id, repository_id, pull_request_id FROM event_log")
            )
        ).one()
    assert tuple(row) == (
        ids["user"],
        None if kind == "multiple" else ids["repository"],
        ids["pull_request"] if kind in {"message", "me_message", "edit"} else None,
    )
    if kind == "me_message":
        async with transaction() as conn:
            link = (
                await conn.execute(text("SELECT thread_ts, pr_url FROM slack_pull_request_link"))
            ).one()
        assert tuple(link) == ("1786573300.000000", "https://github.com/acme/widgets/pull/7")
