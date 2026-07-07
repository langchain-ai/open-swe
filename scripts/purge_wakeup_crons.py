"""One-time backfill: delete expired ``thread_wakeup`` crons from a deployment.

One-shot wakeup crons set an ``end_time`` that stops them re-firing, but the
cron row is never removed, so dead rows accumulate. The ``schedule_thread_wakeup``
tool now purges these opportunistically; this script clears the backlog.

Usage:
    uv run python scripts/purge_wakeup_crons.py --dry-run
    uv run python scripts/purge_wakeup_crons.py

Resolves the deployment URL from ``--url`` or ``LANGGRAPH_URL`` / ``LANGGRAPH_URL_PROD``,
and the API key from ``LANGGRAPH_API_KEY`` / ``LANGSMITH_API_KEY`` / ``LANGSMITH_API_KEY_PROD``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime

from langgraph_sdk import get_client

from agent.tools.schedule_thread_wakeup import (
    find_expired_wakeup_cron_ids,
    purge_expired_wakeup_crons,
)

logger = logging.getLogger(__name__)


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _resolve_url(arg_url: str | None) -> str:
    url = arg_url or os.environ.get("LANGGRAPH_URL") or os.environ.get("LANGGRAPH_URL_PROD")
    if not url:
        raise RuntimeError("Set --url or LANGGRAPH_URL / LANGGRAPH_URL_PROD")
    return url


def _resolve_api_key() -> str | None:
    return (
        os.environ.get("LANGGRAPH_API_KEY")
        or os.environ.get("LANGSMITH_API_KEY")
        or os.environ.get("LANGSMITH_API_KEY_PROD")
    )


async def _run(url: str, api_key: str | None, dry_run: bool) -> None:
    client = get_client(url=url, api_key=api_key)
    now = datetime.now(UTC)
    if dry_run:
        expired = await find_expired_wakeup_cron_ids(client, now=now)
        logger.info("[dry-run] %d expired thread_wakeup cron(s) would be deleted", len(expired))
        for cron_id in expired:
            logger.info("  %s", cron_id)
        return
    deleted = await purge_expired_wakeup_crons(client, now=now)
    logger.info("Deleted %d expired thread_wakeup cron(s)", deleted)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purge expired thread_wakeup crons.")
    parser.add_argument("--url", default=None, help="Deployment URL (defaults to env).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the crons that would be deleted without deleting them.",
    )
    return parser.parse_args()


def main() -> None:
    _load_dotenv_if_available()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    asyncio.run(_run(_resolve_url(args.url), _resolve_api_key(), args.dry_run))


if __name__ == "__main__":
    main()
