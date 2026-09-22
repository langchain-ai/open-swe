"""Seed one PR's human turns into the local transcript tables.

The transcript tables are populated in production by live runs; a local checkout
has none, so the reviewer has no steering history to read and the guidance card
has nothing to show. This writes the human turns of a real conversation and
links them to a real PR. Extraction is not done here — the reviewer decides
which turns affected the pull request, through ``record_guidance``.

    uv run --env-file .env python scripts/seed_local_author_guidance.py \
        <owner> <repo> <number> <turns.json>

``turns.json`` is a list of the messages a person typed, oldest first.
"""

import asyncio
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from agent.database import postgres
from agent.github.pull_requests import PullRequest
from agent.input_messages import human_input
from agent.review.author_guidance import load_steering_history
from agent.thread_ids import pr_comment_thread_id


def github_login() -> str:
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "local-dev"


async def seed_transcript(thread_id: str, login: str, turns: list[str]) -> None:
    started = datetime.now(UTC) - timedelta(minutes=len(turns) * 7)
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO thread (thread_id, version, status, title, metadata)
                VALUES (:thread_id, :version, 'idle', :title, '{}'::jsonb)
                ON CONFLICT (thread_id) DO UPDATE SET version = EXCLUDED.version
                """
            ),
            {"thread_id": thread_id, "version": len(turns), "title": "Local guidance seed"},
        )
        await conn.execute(
            text("DELETE FROM thread_message WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        await conn.execute(
            text("DELETE FROM thread_turn WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        for index, body in enumerate(turns):
            turn_id = uuid.uuid7()
            occurred = started + timedelta(minutes=index * 7)
            await conn.execute(
                text(
                    """
                    INSERT INTO thread_turn (turn_id, thread_id, state, requested_at, completed_at)
                    VALUES (:turn_id, :thread_id, 'completed', :at, :at)
                    """
                ),
                {"turn_id": turn_id, "thread_id": thread_id, "at": occurred},
            )
            envelope = human_input(
                body,
                {"sender_id": f"person:{login}", "surface": "dashboard", "kind": "human"},
            )["content"]
            await conn.execute(
                text(
                    """
                    INSERT INTO thread_message (
                        thread_id, message_id, turn_id, version, role, text, sender, created_at
                    )
                    VALUES (
                        :thread_id, :message_id, :turn_id, :version, 'human', :text,
                        :sender, :created_at
                    )
                    """
                ),
                {
                    "thread_id": thread_id,
                    "message_id": f"seed-{index}",
                    "turn_id": turn_id,
                    "version": index + 1,
                    "text": envelope,
                    "sender": json.dumps({"login": login, "kind": "dashboard"}),
                    "created_at": occurred,
                },
            )


async def main() -> int:
    owner, repo, number, turns_path = sys.argv[1:5]
    turns = json.loads(open(turns_path, encoding="utf-8").read())
    login = github_login()
    thread_id = pr_comment_thread_id(owner, repo, int(number))

    await seed_transcript(thread_id, login, turns)
    pull_request = await PullRequest(owner=owner, repo=repo, number=int(number)).save()
    await pull_request.link_thread(thread_id, source="local-seed")

    history = await load_steering_history(owner, repo, int(number))
    if history is None:
        print("no steering history read back")
        return 1
    print(f"seeded {len(history.follow_ups)} follow-up(s) as {login} on {owner}/{repo}#{number}")
    print(f"request: {history.request.text[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
