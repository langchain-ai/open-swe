"""Slack mock — routes under /slack.

Mirrors the slice of Slack's Web API the open-swe agent actually uses plus a
set of UI endpoints so a browser can post messages as a fake user. State is
persisted in SQLite (see db.py).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from . import db, events

logger = logging.getLogger(__name__)

BOT_USER_ID = os.environ.get("SLACK_BOT_USER_ID", "BOPENSWE01")
BOT_USERNAME = os.environ.get("SLACK_BOT_USERNAME", "openswe-bot")
SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "dev-secret")
FORWARD_URL = os.environ.get(
    "FAKE_SLACK_FORWARD_URL", "http://localhost:2025/webhooks/slack"
)

CHANNEL_ID = "C0GENERAL1"
CHANNEL_NAME = "general"
DM_CHANNEL_ID = "DOPENSWE01"
DEFAULT_HUMAN_USER_ID = "ULOCAL0001"

router = APIRouter(prefix="/slack")


def _resolve_local_identity() -> tuple[str, str, str]:
    """Resolve (short_name, display_name, email) for the local dev user.

    Pulled from ``gh api user`` so the fake Slack seed matches whoever is
    running the stack. Falls back to a generic identity if ``gh`` isn't
    installed or isn't authenticated.
    """
    import json as _json
    import subprocess as _subprocess
    try:
        out = _subprocess.run(
            ["gh", "api", "user"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        data = _json.loads(out)
        login = data.get("login") or "local-user"
        name = data.get("name") or login
        email = data.get("email") or f"{login}@local"
        return login, name, email
    except Exception:
        return "local-user", "Local User", "local@local"


# --- seed -------------------------------------------------------------------


async def seed() -> None:
    """Insert canonical Slack users on first boot if absent."""
    short_name, display_name, email = _resolve_local_identity()
    for row in (
        (BOT_USER_ID, BOT_USERNAME, "Open SWE", BOT_USERNAME, "", 1),
        (DEFAULT_HUMAN_USER_ID, short_name, display_name, short_name, email, 0),
    ):
        await db.execute(
            "INSERT OR IGNORE INTO slack_users "
            "(id, name, real_name, display_name, email, is_bot) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )


# --- helpers ----------------------------------------------------------------


def now_ts() -> str:
    return f"{time.time():.6f}"


async def _serialize_message(row: dict[str, Any]) -> dict[str, Any]:
    user = await db.fetchone("SELECT * FROM slack_users WHERE id = ?", (row["user_id"],))
    display = (user or {}).get("display_name") or (user or {}).get("name") or row["user_id"]
    reaction_rows = await db.fetchall(
        "SELECT name, COUNT(*) AS count, GROUP_CONCAT(user_id) AS user_ids "
        "FROM slack_reactions WHERE message_ts = ? GROUP BY name ORDER BY name",
        (row["ts"],),
    )
    reactions = [
        {"name": r["name"], "count": r["count"], "users": (r["user_ids"] or "").split(",")}
        for r in reaction_rows
    ]
    return {
        "ts": row["ts"],
        "channel_id": row["channel_id"],
        "user": row["user_id"],
        "user_name": display,
        "text": row["text"],
        "thread_ts": row["thread_ts"],
        "parent_user_id": row["parent_user_id"],
        "is_bot": bool((user or {}).get("is_bot", 0)),
        "reactions": reactions,
    }


async def _append_message(
    *,
    ts: str,
    channel_id: str,
    user_id: str,
    text: str,
    thread_ts: str | None,
    parent_user_id: str | None,
) -> dict[str, Any]:
    await db.execute(
        "INSERT INTO slack_messages "
        "(ts, channel_id, user_id, text, thread_ts, parent_user_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, channel_id, user_id, text, thread_ts, parent_user_id, time.time()),
    )
    row = await db.fetchone("SELECT * FROM slack_messages WHERE ts = ?", (ts,))
    assert row
    serialized = await _serialize_message(row)
    serialized["channel_id"] = channel_id
    await events.broadcast({"type": "slack_message_added", "message": serialized})
    # Mirror real Slack: when the app sends a reply, the
    # assistant.threads.setStatus indicator auto-clears. For thread replies
    # that's the thread_ts; for top-level bot posts (e.g. slackv2 DM), clear
    # every pending status for this channel so DM status indicators go away.
    user = await db.fetchone("SELECT is_bot FROM slack_users WHERE id = ?", (user_id,))
    if user and user.get("is_bot"):
        if thread_ts:
            await _set_thread_status(channel_id, thread_ts, "")
        else:
            pending = await db.fetchall(
                "SELECT thread_ts FROM slack_thread_status WHERE channel_id = ?",
                (channel_id,),
            )
            for r in pending:
                await _set_thread_status(channel_id, r["thread_ts"], "")
    return serialized


async def _set_thread_status(channel_id: str, thread_ts: str, status: str) -> None:
    if status:
        await db.execute(
            "INSERT OR REPLACE INTO slack_thread_status "
            "(channel_id, thread_ts, status, updated_at) VALUES (?, ?, ?, ?)",
            (channel_id, thread_ts, status, time.time()),
        )
    else:
        await db.execute(
            "DELETE FROM slack_thread_status WHERE channel_id = ? AND thread_ts = ?",
            (channel_id, thread_ts),
        )
    await events.broadcast(
        {
            "type": "slack_thread_status",
            "channel": channel_id,
            "thread_ts": thread_ts,
            "status": status,
            "bot_username": BOT_USERNAME,
        }
    )


async def _resolve_parent_user(thread_ts: str | None) -> str | None:
    if not thread_ts:
        return None
    row = await db.fetchone("SELECT user_id FROM slack_messages WHERE ts = ?", (thread_ts,))
    return row["user_id"] if row else None


async def _parse_slack_body(request: Request) -> dict[str, Any]:
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        return await request.json()
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


# --- UI ---------------------------------------------------------------------


@router.get("/ui/state")
async def ui_state() -> dict[str, Any]:
    users = await db.fetchall("SELECT * FROM slack_users ORDER BY is_bot, name")
    messages = await db.fetchall(
        "SELECT * FROM slack_messages ORDER BY ts"
    )
    msgs = [await _serialize_message(r) for r in messages]
    users_by_id = {
        u["id"]: {
            "id": u["id"],
            "name": u["name"],
            "real_name": u["real_name"],
            "profile": {
                "display_name": u["display_name"],
                "real_name": u["real_name"],
                "email": u["email"],
            },
        }
        for u in users
    }
    status_rows = await db.fetchall(
        "SELECT channel_id, thread_ts, status FROM slack_thread_status"
    )
    thread_statuses = {
        f"{r['channel_id']}/{r['thread_ts']}": r["status"] for r in status_rows
    }
    channels = [
        {"id": CHANNEL_ID, "name": CHANNEL_NAME, "kind": "channel"},
        {"id": DM_CHANNEL_ID, "name": BOT_USERNAME, "kind": "im"},
    ]
    return {
        "channel": {"id": CHANNEL_ID, "name": CHANNEL_NAME},
        "channels": channels,
        "dm_channel_id": DM_CHANNEL_ID,
        "bot_user_id": BOT_USER_ID,
        "bot_username": BOT_USERNAME,
        "current_user_id": DEFAULT_HUMAN_USER_ID,
        "users": users_by_id,
        "messages": msgs,
        "thread_statuses": thread_statuses,
    }


@router.post("/ui/send")
async def ui_send(request: Request) -> dict[str, Any]:
    body = await request.json()
    text = body.get("text", "")
    thread_ts = body.get("thread_ts") or None
    channel_id = body.get("channel_id") or CHANNEL_ID
    is_dm = channel_id == DM_CHANNEL_ID
    ts = now_ts()
    parent_user_id = await _resolve_parent_user(thread_ts)
    await _append_message(
        ts=ts,
        channel_id=channel_id,
        user_id=DEFAULT_HUMAN_USER_ID,
        text=text,
        thread_ts=thread_ts,
        parent_user_id=parent_user_id,
    )

    if is_dm:
        event_type = "message"
    else:
        event_type = "app_mention" if f"<@{BOT_USER_ID}>" in text else "message"
    event: dict[str, Any] = {
        "type": event_type,
        "channel": channel_id,
        "channel_type": "im" if is_dm else "channel",
        "user": DEFAULT_HUMAN_USER_ID,
        "text": text,
        "ts": ts,
        "event_ts": ts,
    }
    if thread_ts:
        event["thread_ts"] = thread_ts
        if parent_user_id:
            event["parent_user_id"] = parent_user_id
    payload = {
        "type": "event_callback",
        "event": event,
        "authorizations": [{"user_id": BOT_USER_ID}],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:{raw_body.decode('utf-8')}".encode()
    signature = (
        "v0="
        + hmac.new(SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256).hexdigest()
    )

    forwarded_status: int | None = None
    forwarded_response: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                FORWARD_URL,
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Slack-Signature": signature,
                    "X-Slack-Request-Timestamp": timestamp,
                },
            )
        forwarded_status = resp.status_code
        try:
            forwarded_response = resp.json()
        except Exception:  # noqa: BLE001
            forwarded_response = {"body": resp.text}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Forward to %s failed", FORWARD_URL)
        forwarded_response = {"error": str(exc)}

    await db.execute(
        "INSERT INTO webhook_log (target, event_type, status, response, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "slack",
            event_type,
            forwarded_status or 0,
            json.dumps(forwarded_response),
            time.time(),
        ),
    )
    await events.broadcast(
        {
            "type": "webhook_result",
            "target": "slack",
            "ts": ts,
            "status": forwarded_status,
            "response": forwarded_response,
        }
    )
    return {
        "ok": True,
        "ts": ts,
        "forwarded_status": forwarded_status,
        "forwarded_response": forwarded_response,
    }


@router.post("/ui/reset")
async def ui_reset() -> dict[str, Any]:
    await db.execute("DELETE FROM slack_messages")
    await db.execute("DELETE FROM slack_reactions")
    await db.execute("DELETE FROM slack_thread_status")
    await events.broadcast({"type": "slack_reset"})
    return {"ok": True}


# --- Slack Web API mocks ----------------------------------------------------


@router.post("/api/chat.postMessage")
async def chat_post_message(request: Request) -> dict[str, Any]:
    data = await _parse_slack_body(request)
    channel = data.get("channel") or CHANNEL_ID
    text = data.get("text", "")
    thread_ts = data.get("thread_ts") or None
    ts = now_ts()
    parent_user_id = await _resolve_parent_user(thread_ts)
    await _append_message(
        ts=ts,
        channel_id=channel,
        user_id=BOT_USER_ID,
        text=text,
        thread_ts=thread_ts,
        parent_user_id=parent_user_id,
    )
    return {
        "ok": True,
        "channel": channel,
        "ts": ts,
        "message": {"text": text, "ts": ts, "user": BOT_USER_ID},
    }


@router.post("/api/chat.postEphemeral")
async def chat_post_ephemeral(request: Request) -> dict[str, Any]:
    data = await _parse_slack_body(request)
    await events.broadcast(
        {
            "type": "slack_ephemeral",
            "channel": data.get("channel"),
            "user": data.get("user"),
            "text": data.get("text", ""),
            "thread_ts": data.get("thread_ts"),
        }
    )
    return {"ok": True, "message_ts": now_ts()}


@router.post("/api/assistant.threads.setStatus")
async def assistant_threads_set_status(request: Request) -> dict[str, Any]:
    data = await _parse_slack_body(request)
    channel_id = data.get("channel_id") or data.get("channel") or CHANNEL_ID
    thread_ts = data.get("thread_ts") or ""
    status = data.get("status") or ""
    if not thread_ts:
        return {"ok": False, "error": "missing_thread_ts"}
    await _set_thread_status(channel_id, thread_ts, status)
    return {"ok": True}


@router.post("/api/reactions.add")
async def reactions_add(request: Request) -> dict[str, Any]:
    data = await _parse_slack_body(request)
    message_ts = data.get("timestamp") or data.get("ts") or ""
    name = data.get("name") or "eyes"
    await db.execute(
        "INSERT OR IGNORE INTO slack_reactions "
        "(message_ts, user_id, name, created_at) VALUES (?, ?, ?, ?)",
        (message_ts, BOT_USER_ID, name, time.time()),
    )
    await events.broadcast(
        {
            "type": "slack_reaction",
            "channel": data.get("channel"),
            "ts": message_ts,
            "user_id": BOT_USER_ID,
            "name": name,
        }
    )
    return {"ok": True}


@router.api_route("/api/users.info", methods=["GET", "POST"])
async def users_info(request: Request) -> dict[str, Any]:
    if request.method == "GET":
        user_id = request.query_params.get("user", "")
    else:
        data = await _parse_slack_body(request)
        user_id = data.get("user", "")
    user = await db.fetchone("SELECT * FROM slack_users WHERE id = ?", (user_id,))
    if not user:
        return {"ok": False, "error": "user_not_found"}
    return {
        "ok": True,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "real_name": user["real_name"],
            "profile": {
                "display_name": user["display_name"],
                "real_name": user["real_name"],
                "email": user["email"],
            },
        },
    }


@router.api_route("/api/conversations.replies", methods=["GET", "POST"])
async def conversations_replies(request: Request) -> dict[str, Any]:
    if request.method == "GET":
        ts = request.query_params.get("ts", "")
    else:
        data = await _parse_slack_body(request)
        ts = data.get("ts", "")
    rows = await db.fetchall(
        "SELECT * FROM slack_messages WHERE ts = ? OR thread_ts = ? ORDER BY ts",
        (ts, ts),
    )
    msgs = [
        {
            "ts": r["ts"],
            "user": r["user_id"],
            "text": r["text"],
            "thread_ts": r["thread_ts"] or ts,
        }
        for r in rows
    ]
    return {"ok": True, "messages": msgs, "has_more": False}


@router.get("/ui/events")
async def ui_events(request: Request) -> StreamingResponse:
    return StreamingResponse(events.sse_stream(request), media_type="text/event-stream")
