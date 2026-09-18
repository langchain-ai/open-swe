"""Rebuild one or more threads' transcript projections from their event log.

The read tables are a fold over ``thread_event``; this throws a thread's away
and folds them again, which is the repair for a projection that drifted from
the log. Attachments and tool outputs live beside the log and are untouched,
and ``thread.version`` does not move.

Usage:
    uv run python scripts/rebuild_transcript_projections.py <thread_id> [<thread_id> ...]

Reads the database from ``POSTGRES_URI``.
"""

import argparse
import asyncio
import logging

from agent.database import postgres
from agent.transcript.rebuild import rebuild_thread_projections

logger = logging.getLogger(__name__)


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


async def _run(thread_ids: list[str]) -> int:
    postgres.require_configured()
    failures = 0
    try:
        for thread_id in thread_ids:
            try:
                replayed = await rebuild_thread_projections(thread_id)
            except Exception:
                failures += 1
                logger.exception(
                    "Rebuilding transcript projections failed",
                    extra={"transcript": {"thread_id": thread_id}},
                )
                print(f"{thread_id}: failed")
                continue
            print(f"{thread_id}: rebuilt from {replayed} event(s)")
    finally:
        await postgres.close()
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild transcript projections for the given threads."
    )
    parser.add_argument("thread_ids", nargs="+", help="Thread ids to rebuild.")
    return parser.parse_args()


def main() -> None:
    _load_dotenv_if_available()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    raise SystemExit(asyncio.run(_run(args.thread_ids)))


if __name__ == "__main__":
    main()
