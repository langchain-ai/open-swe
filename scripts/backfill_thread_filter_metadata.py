"""One-time backfill: stamp the searchable filter keys onto existing threads.

The sidebar filters on "unresolved", "in this repo", "has no repo" and "not an
automation". None of those are expressible as a metadata containment match, so
the thread list used to fetch pages and filter them in Python. New threads now
carry ``repo_key``, ``is_automation`` and ``resolved`` (see
``agent.utils.thread_filters``); this fills them in for every thread that
predates that, and records the completion that lets the thread list push those
filters into the search.

Usage:
    uv run python scripts/backfill_thread_filter_metadata.py --dry-run
    uv run python scripts/backfill_thread_filter_metadata.py

Resolves the deployment URL from ``--url`` or ``LANGGRAPH_URL``, and the API key
from ``LANGGRAPH_API_KEY`` / ``LANGSMITH_API_KEY``.
"""

import argparse
import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client

from agent.config import ENV
from agent.utils.thread_filters import (
    FILTER_BACKFILL_KEY,
    FILTER_BACKFILL_NAMESPACE,
    derive_filter_metadata,
    filter_backfill_record,
    filter_metadata_is_current,
)

logger = logging.getLogger(__name__)

_PAGE = 100
_UPDATE_CONCURRENCY = 8


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


async def _backfill_page(client: Any, threads: list[Mapping[str, Any]], *, dry_run: bool) -> int:
    stale = [
        thread
        for thread in threads
        if isinstance(thread.get("metadata"), Mapping)
        and not filter_metadata_is_current(thread["metadata"])
    ]
    if dry_run or not stale:
        return len(stale)
    semaphore = asyncio.Semaphore(_UPDATE_CONCURRENCY)

    async def update(thread: Mapping[str, Any]) -> None:
        async with semaphore:
            await client.threads.update(
                thread_id=thread["thread_id"],
                metadata=derive_filter_metadata(thread["metadata"]),
            )

    await asyncio.gather(*(update(thread) for thread in stale))
    return len(stale)


async def _run(url: str, api_key: str | None, dry_run: bool) -> None:
    client = get_client(url=url, api_key=api_key)
    scanned = updated = offset = 0
    while True:
        page = await client.threads.search(
            metadata={},
            limit=_PAGE,
            offset=offset,
            sort_by="created_at",
            sort_order="desc",
            select=["thread_id", "metadata"],
        )
        threads = [thread for thread in page or [] if isinstance(thread, Mapping)]
        if not threads:
            break
        scanned += len(threads)
        updated += await _backfill_page(client, threads, dry_run=dry_run)
        logger.info("scanned %d thread(s), %d needed the filter keys", scanned, updated)
        if len(threads) < _PAGE:
            break
        offset += len(threads)
    if dry_run:
        logger.info("[dry-run] %d of %d thread(s) would be updated", updated, scanned)
        return
    await client.store.put_item(
        FILTER_BACKFILL_NAMESPACE,
        FILTER_BACKFILL_KEY,
        filter_backfill_record(threads_scanned=scanned, threads_updated=updated),
    )
    logger.info(
        "Backfilled %d of %d thread(s); the thread list can now filter in the search",
        updated,
        scanned,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill thread filter metadata.")
    parser.add_argument("--url", default=None, help="Deployment URL (defaults to env).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count the threads that need the keys without writing anything.",
    )
    return parser.parse_args()


def main() -> None:
    _load_dotenv_if_available()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    asyncio.run(_run(_resolve_url(args.url), _resolve_api_key(), args.dry_run))


if __name__ == "__main__":
    main()
