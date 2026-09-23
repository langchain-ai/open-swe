"""One-time repair: give every listed thread the ``thread_category`` it should carry.

The thread list searches interactive threads by category so LangGraph's
metadata index can skip automation threads. Two kinds of thread defeat that
until they expire: automation threads a Slack mention relabelled
``interactive`` (``upsert_agent_thread_metadata`` now keeps a thread's
category), and threads created without a category. This stamps both.

The thread list pages by ``state_updated_at``, which metadata updates leave
alone, so stamping does not reorder it. The scan reads every thread in the
deployment and takes a while on large ones; a thread deleted during it (a TTL
sweep) shifts the pages, so run it again until it finds nothing.

Usage:
    uv run python scripts/repair_thread_category.py            # count only
    uv run python scripts/repair_thread_category.py --apply

Resolves the deployment URL from ``--url`` or ``LANGGRAPH_URL``, and the API key
from ``LANGGRAPH_API_KEY`` / ``LANGSMITH_API_KEY``.
"""

import argparse
import asyncio
import logging
import os
from collections import Counter
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from agent.config import ENV
from agent.threads.summary import thread_classification, thread_source
from agent.utils.json_types import thread_metadata
from agent.utils.thread_participants import PARTICIPANT_EMAILS_KEY, PARTICIPANT_LOGINS_KEY

logger = logging.getLogger(__name__)

_PAGE = 500
# What a viewer's thread list searches by; without one, only the admin
# all-threads view lists a thread, and it does not filter by category.
_LISTING_IDENTITY_KEYS = (
    PARTICIPANT_LOGINS_KEY,
    PARTICIPANT_EMAILS_KEY,
    "github_login",
    "triggering_user_email",
)


def repaired_category(metadata: Mapping[str, Any]) -> str | None:
    """The category to stamp on a thread, or None when it needs no change."""
    current = metadata.get("thread_category")
    # Only automations carry these, and a Slack mention never overwrote them.
    if any(
        isinstance(value, str) and value.strip()
        for value in (metadata.get("schedule_id"), metadata.get("automation_scope"))
    ):
        return None if current == "automation" else "automation"
    if current:
        return None
    listed = thread_source(metadata) == "schedule" or any(
        metadata.get(key) for key in _LISTING_IDENTITY_KEYS
    )
    return thread_classification(metadata)[0] if listed else None


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _resolve_url(arg_url: str | None) -> str:
    url = arg_url or ENV.LANGGRAPH_URL.optional()
    if not url:
        raise RuntimeError("Set --url or LANGGRAPH_URL")
    return url


def _resolve_api_key() -> str | None:
    return os.environ.get("LANGGRAPH_API_KEY") or ENV.LANGSMITH_API_KEY.optional()


async def _threads_to_repair(client: LangGraphClient) -> tuple[int, list[tuple[str, str]]]:
    scanned = 0
    repairs: list[tuple[str, str]] = []
    while batch := await client.threads.search(
        limit=_PAGE,
        offset=scanned,
        sort_by="created_at",
        sort_order="asc",
        select=["thread_id", "metadata"],
    ):
        scanned += len(batch)
        for thread in batch:
            thread_id = thread.get("thread_id")
            category = repaired_category(thread_metadata(thread))
            if isinstance(thread_id, str) and thread_id and category:
                repairs.append((thread_id, category))
    return scanned, repairs


async def _run(url: str, api_key: str | None, apply: bool) -> int:
    client = get_client(url=url, api_key=api_key)
    scanned, repairs = await _threads_to_repair(client)
    by_category = Counter(category for _, category in repairs)
    print(f"Scanned {scanned} thread(s); {len(repairs)} need a category: {dict(by_category)}")
    if not apply:
        return 0
    failures = 0
    for thread_id, category in repairs:
        try:
            await client.threads.update(thread_id=thread_id, metadata={"thread_category": category})
        except Exception:
            failures += 1
            logger.exception(
                "Stamping thread category failed",
                extra={"thread_repair": {"thread_id": thread_id, "category": category}},
            )
    print(f"Stamped {len(repairs) - failures} thread(s); {failures} failed")
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stamp missing or relabelled thread categories.")
    parser.add_argument("--url", default=None, help="Deployment URL (defaults to env).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Stamp the categories; without it the script only counts.",
    )
    return parser.parse_args()


def main() -> None:
    _load_dotenv_if_available()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    raise SystemExit(asyncio.run(_run(_resolve_url(args.url), _resolve_api_key(), args.apply)))


if __name__ == "__main__":
    main()
