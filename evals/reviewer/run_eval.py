"""Run the reviewer eval against the LangSmith dataset.

Usage:
    uv run python -m evals.reviewer.run_eval \\
        --dataset-name openswe-reviewer-v1 \\
        --experiment-prefix openswe-reviewer-baseline \\
        --max-concurrency 5
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable

from dotenv import load_dotenv
from langgraph_sdk import get_client
from langsmith import Client, aevaluate
from langsmith.schemas import Example

from evals.reviewer.judge import aggregate_pr, judge_match
from evals.reviewer.target import LANGGRAPH_URL, drain_thread_ids, review_pr

load_dotenv()

logger = logging.getLogger(__name__)


async def _cleanup_threads(thread_ids: Iterable[str]) -> None:
    """Delete LangGraph threads created during the eval.

    Underlying sandboxes are reclaimed by the provider's TTL — this only
    drops the LangGraph checkpoint/metadata records.
    """
    sdk = get_client(url=LANGGRAPH_URL)
    for tid in thread_ids:
        try:
            await sdk.threads.delete(tid)
        except Exception as exc:
            logger.warning("Failed to delete thread %s: %s", tid, exc)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-name", default="openswe-reviewer-v1")
    ap.add_argument("--experiment-prefix", default="openswe-reviewer-baseline")
    ap.add_argument("--max-concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None, help="Run only the first N examples.")
    ap.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip deleting LangGraph threads after the experiment finishes.",
    )
    args = ap.parse_args()

    data: str | list[Example]
    if args.limit:
        client = Client()
        data = list(client.list_examples(dataset_name=args.dataset_name, limit=args.limit))
    else:
        data = args.dataset_name

    try:
        await aevaluate(
            review_pr,
            data=data,
            evaluators=[judge_match],
            summary_evaluators=[aggregate_pr],
            experiment_prefix=args.experiment_prefix,
            max_concurrency=args.max_concurrency,
            num_repetitions=1,
        )
    finally:
        if not args.no_cleanup:
            thread_ids = drain_thread_ids()
            if thread_ids:
                logger.info("Cleaning up %d LangGraph threads", len(thread_ids))
                await _cleanup_threads(thread_ids)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
