import json
from datetime import date
from typing import Literal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text
from starlette.requests import Request
from starlette.types import Message

from agent.database import transaction
from agent.slack import routes
from agent.slack.payloads import SlackChannelContext
from agent.webhooks import common, event_log
from agent.webhooks.event_log import EventLog, EventRefs


async def _partitions() -> set[str]:
    async with transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'event_log'::regclass"
            )
        )
        return set(rows.scalars().all())


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
    monkeypatch.setattr(event_log, "_ROTATED_AT", None)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/commands",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        }
    )

    await EventLog.record(
        request,
        b"command=%2Foswe&text=hello+there",
        "slack",
        event_type="/oswe",
        delivery_id="trigger-1",
    )

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


@pytest.mark.parametrize("kind", ["message", "edit", "multiple", "unknown"])
async def test_slack_event_links_a_single_known_pr_without_dispatching(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["message", "edit", "multiple", "unknown"],
) -> None:
    monkeypatch.setattr(event_log, "_ROTATED_AT", None)
    monkeypatch.setattr(common, "verify_slack_signature", lambda **kwargs: True)
    monkeypatch.setattr("agent.incidents.channels.handle_slack_event", AsyncMock(return_value=None))
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
        ids["pull_request"] if kind in {"message", "edit"} else None,
    )
