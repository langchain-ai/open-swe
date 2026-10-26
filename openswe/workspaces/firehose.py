"""Workspace thread announcements, delivered through the durable job queue."""

import json
from collections.abc import Mapping
from uuid import uuid7

from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.database.postgres import transaction
from openswe.jobs.queue import Job
from openswe.slack.client import post_slack_top_level_message_with_ts
from openswe.source_context import SourceContext
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.workspaces.store import WORKSPACES

JOB_KIND = "workspace_firehose"
DELIVERED_KEY = "workspace_firehose_delivered"


async def enqueue(conn: AsyncConnection, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
    if not metadata.get("workspace") or metadata.get(DELIVERED_KEY):
        return
    await conn.execute(
        text(
            "INSERT INTO job (id, kind, payload, key, due_at) "
            "SELECT :id, :kind, CAST(:payload AS jsonb), :key, clock_timestamp() "
            "FROM workspace WHERE slug = :slug AND firehose_channel_id IS NOT NULL "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {
            "slug": metadata["workspace"],
            "id": uuid7(),
            "kind": JOB_KIND,
            "payload": json.dumps({"thread_id": thread_id}),
            "key": f"{JOB_KIND}:{thread_id}",
        },
    )


async def deliver(job: Job) -> None:
    thread_id = job.payload.get("thread_id")
    if not isinstance(thread_id, str):
        return
    async with transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"{JOB_KIND}:{thread_id}"},
        )
        metadata = await conn.scalar(
            text("SELECT metadata FROM thread_index WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        if not isinstance(metadata, dict) or metadata.get(DELIVERED_KEY):
            return
        slug = metadata.get("workspace")
        workspace = await WORKSPACES.get(slug) if isinstance(slug, str) else None
        if workspace is None or not workspace.firehose_channel_id:
            return
        private = metadata.get("visibility", "public") != "public"
        title = "Private thread" if private else str(metadata.get("title") or "New thread")
        title = title[:160].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        web_url = dashboard_thread_url(thread_id)
        if web_url is None:
            raise RuntimeError("Workspace firehose requires a dashboard URL")
        links = [f"<{web_url}|Web>"]
        slack = SourceContext.from_metadata(metadata).slack_thread
        if not private and slack and slack.location:
            channel_id, thread_ts = slack.location
            links.append(
                f"<https://slack.com/archives/{channel_id}/p{thread_ts.replace('.', '')}|Slack>"
            )
        posted = await post_slack_top_level_message_with_ts(
            workspace.firehose_channel_id,
            f"{title}\n{' · '.join(links)}",
            unfurl_links=False,
            unfurl_media=False,
        )
        if not posted:
            raise RuntimeError("Workspace firehose announcement was not delivered")
        await conn.execute(
            text(
                "UPDATE thread_index SET metadata = metadata || CAST(:marker AS jsonb) "
                "WHERE thread_id = :thread_id"
            ),
            {"thread_id": thread_id, "marker": json.dumps({DELIVERED_KEY: True})},
        )
