"""Export unique PR URLs from commit_and_open_pr tool messages in LangGraph threads."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, convert_to_messages
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_PATH = "pr_urls.json"
DEFAULT_PAGE_SIZE = 100
DEFAULT_CONCURRENCY = 20
DEFAULT_DAYS_BACK = 9


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def get_langgraph_url(explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url
    return os.environ.get("LANGGRAPH_URL") or os.environ.get(
        "LANGGRAPH_URL_PROD", "http://localhost:2024"
    )


def extract_pr_urls_from_messages(messages: list[BaseMessage]) -> list[str]:
    pr_urls: list[str] = []

    for message in messages:
        if getattr(message, "type", None) != "tool":
            continue
        if getattr(message, "name", None) != "commit_and_open_pr":
            continue

        content = getattr(message, "content", None)
        payload: dict[str, Any] | None = None
        if isinstance(content, str):
            try:
                parsed_content = json.loads(content)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed_content, dict):
                payload = parsed_content
        elif isinstance(content, dict):
            payload = content

        if not payload:
            continue

        pr_url = payload.get("pr_url")
        if isinstance(pr_url, str) and pr_url:
            pr_urls.append(pr_url)

    return pr_urls


def extract_pr_urls_from_state_values(state_values: Any) -> list[str]:
    if not isinstance(state_values, dict):
        return []

    raw_messages = state_values.get("messages")
    if not isinstance(raw_messages, list):
        return []

    try:
        messages = convert_to_messages(raw_messages)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to deserialize messages from thread state")
        raise ValueError("Failed to deserialize messages from thread state") from None

    return extract_pr_urls_from_messages(messages)


def _get_thread_id(thread: Any) -> str | None:
    if isinstance(thread, dict):
        thread_id = thread.get("thread_id")
    else:
        thread_id = getattr(thread, "thread_id", None)
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    return None


def _get_thread_created_at(thread: Any) -> datetime | None:
    if isinstance(thread, dict):
        created_at = thread.get("created_at")
    else:
        created_at = getattr(thread, "created_at", None)
    return _coerce_datetime(created_at)


def _split_recent_threads(threads: list[Any], cutoff: datetime) -> tuple[list[Any], bool]:
    recent_threads: list[Any] = []

    for thread in threads:
        created_at = _get_thread_created_at(thread)
        if created_at is None:
            logger.warning(
                "Skipping thread %s because created_at is missing or invalid",
                _get_thread_id(thread) or "<unknown>",
            )
            continue
        if created_at >= cutoff:
            recent_threads.append(thread)
            continue
        return recent_threads, True

    return recent_threads, False


def _iter_offset_batches(
    total_threads: int, page_size: int, batch_size: int
) -> Iterator[list[int]]:
    offsets = range(0, total_threads, page_size)
    batch: list[int] = []

    for offset in offsets:
        batch.append(offset)
        if len(batch) == batch_size:
            yield batch
            batch = []

    if batch:
        yield batch


async def _fetch_thread_page(
    client: LangGraphClient,
    *,
    offset: int,
    page_size: int,
) -> tuple[int, list[Any]]:
    threads = await client.threads.search(
        limit=page_size,
        offset=offset,
        sort_by="created_at",
        sort_order="desc",
    )
    return offset, threads


async def _fetch_pr_urls_for_thread(
    client: LangGraphClient,
    thread_id: str,
    semaphore: asyncio.Semaphore,
) -> list[str]:
    async with semaphore:
        try:
            state = await client.threads.get_state(thread_id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to fetch state for thread %s", thread_id)
            return []

        return extract_pr_urls_from_state_values(state.get("values"))


async def export_pr_urls(
    *,
    langgraph_url: str,
    output_path: Path,
    page_size: int = DEFAULT_PAGE_SIZE,
    concurrency: int = DEFAULT_CONCURRENCY,
    days_back: int = DEFAULT_DAYS_BACK,
) -> list[str]:
    if page_size < 1:
        raise ValueError("page_size must be greater than 0")
    if concurrency < 1:
        raise ValueError("concurrency must be greater than 0")
    if days_back < 1:
        raise ValueError("days_back must be greater than 0")

    api_key = os.environ.get("LANGGRAPH_API_KEY")
    client = get_client(url=langgraph_url, api_key=api_key)
    try:
        total_threads = await client.threads.count()
        cutoff = datetime.now(UTC) - timedelta(days=days_back)
        logger.info(
            "Scanning threads from %s created on or after %s",
            langgraph_url,
            cutoff.isoformat(),
        )

        state_semaphore = asyncio.Semaphore(concurrency)
        unique_pr_urls: set[str] = set()
        recent_threads_count = 0

        for offset_batch in _iter_offset_batches(total_threads, page_size, concurrency):
            page_results = await asyncio.gather(
                *[
                    _fetch_thread_page(client, offset=offset, page_size=page_size)
                    for offset in offset_batch
                ]
            )

            thread_ids: list[str] = []
            saw_older_thread = False
            for _offset, threads in sorted(page_results, key=lambda result: result[0]):
                if not threads:
                    continue

                recent_threads, saw_older_thread = _split_recent_threads(threads, cutoff)
                recent_threads_count += len(recent_threads)

                for thread in recent_threads:
                    thread_id = _get_thread_id(thread)
                    if thread_id:
                        thread_ids.append(thread_id)

                if saw_older_thread:
                    break

            for pr_urls in await asyncio.gather(
                *[
                    _fetch_pr_urls_for_thread(client, thread_id, state_semaphore)
                    for thread_id in thread_ids
                ]
            ):
                unique_pr_urls.update(pr_urls)

            logger.info("Processed %d recent thread(s)", recent_threads_count)

            if saw_older_thread:
                break

        sorted_pr_urls = sorted(unique_pr_urls)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"{json.dumps(sorted_pr_urls, indent=2)}\n", encoding="utf-8")
        logger.info("Total threads in deployment: %d", total_threads)
        logger.info("Threads in last %d days: %d", days_back, recent_threads_count)
        logger.info("Wrote %d unique PR URL(s) to %s", len(sorted_pr_urls), output_path)
        return sorted_pr_urls
    finally:
        await client.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export unique PR URLs from commit_and_open_pr tool messages."
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"Path to the output JSON file. Defaults to {DEFAULT_OUTPUT_PATH!r}.",
    )
    parser.add_argument(
        "--langgraph-url",
        default=None,
        help="LangGraph deployment URL. Defaults to LANGGRAPH_URL or LANGGRAPH_URL_PROD.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Threads to fetch per page. Defaults to {DEFAULT_PAGE_SIZE}.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Concurrent LangGraph page/state requests per batch. Defaults to {DEFAULT_CONCURRENCY}.",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help=f"Only include threads created in the last N days. Defaults to {DEFAULT_DAYS_BACK}.",
    )
    return parser.parse_args()


def main() -> None:
    _load_dotenv_if_available()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    args = parse_args()
    asyncio.run(
        export_pr_urls(
            langgraph_url=get_langgraph_url(args.langgraph_url),
            output_path=Path(args.output),
            page_size=args.page_size,
            concurrency=args.concurrency,
            days_back=args.days_back,
        )
    )


if __name__ == "__main__":
    main()
