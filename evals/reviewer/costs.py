"""Per-PR and per-run LLM cost for a reviewer eval experiment.

The reviewer runs inside the LangGraph server, which traces into its own
LangSmith project, so the experiment's target runs carry no cost. Each target
output names its reviewer thread; this reads that thread's cost from the
server's project, records it as ``cost_usd`` / ``total_tokens`` feedback on the
experiment run, and writes run totals into the experiment metadata.

``run_eval`` calls this when an experiment finishes. Re-run it by hand if
LangSmith was still ingesting traces (re-running overwrites earlier values):

    uv run python -m evals.reviewer.costs --experiment <experiment-name> \\
        --reviewer-project open-swe-local
"""

import argparse
import asyncio
import logging
import os
import statistics
import uuid
from dataclasses import dataclass

from dotenv import load_dotenv
from langsmith import AsyncClient, Client
from langsmith.schemas import Run
from langsmith.utils import LangSmithConflictError

logger = logging.getLogger(__name__)

# LangSmith prices a trace shortly after the server flushes it.
_RETRY_DELAYS_SECONDS = (15, 45, 90)
_MAX_CONCURRENT_LOOKUPS = 8


@dataclass(frozen=True)
class PrCost:
    run_id: uuid.UUID
    pr_url: str
    thread_id: str
    cost_usd: float
    total_tokens: int


@dataclass(frozen=True)
class CostSummary:
    experiment: str
    priced: list[PrCost]
    unpriced: list[str]

    @property
    def total_usd(self) -> float:
        return sum(item.cost_usd for item in self.priced)

    @property
    def mean_usd(self) -> float:
        return self.total_usd / len(self.priced) if self.priced else 0.0

    @property
    def median_usd(self) -> float:
        return statistics.median(item.cost_usd for item in self.priced) if self.priced else 0.0

    @property
    def max_usd(self) -> float:
        return max((item.cost_usd for item in self.priced), default=0.0)

    @property
    def total_tokens(self) -> int:
        return sum(item.total_tokens for item in self.priced)


async def _thread_cost(
    client: AsyncClient, project_id: str, thread_id: str
) -> tuple[float, int] | None:
    for delay in (0, *_RETRY_DELAYS_SECONDS):
        if delay:
            await asyncio.sleep(delay)
        stats = await client.threads.stats(
            thread_id, session_id=project_id, selects=["TOTAL_COST", "TOTAL_TOKENS"]
        )
        if stats.total_cost is not None and float(stats.total_cost) > 0:
            return float(stats.total_cost), int(stats.total_tokens or 0)
    logger.warning("Reviewer thread has no priced traces", extra={"thread_id": thread_id})
    return None


def _write_feedback(
    client: Client,
    experiment_id: uuid.UUID,
    run_id: uuid.UUID,
    key: str,
    *,
    score: float | None = None,
    value: int | None = None,
) -> None:
    feedback_id = uuid.uuid5(run_id, key)
    try:
        client.create_feedback(
            run_id,
            key,
            score=score,
            value=value,
            feedback_id=feedback_id,
            session_id=experiment_id,
        )
    except LangSmithConflictError:
        client.update_feedback(feedback_id, score=score, value=value)


async def record_experiment_costs(experiment: str, reviewer_project: str) -> CostSummary:
    """Price every example of ``experiment`` from ``reviewer_project`` traces."""
    client = Client()
    async_client = AsyncClient()
    reviewer_project_id = str(
        (await asyncio.to_thread(client.read_project, project_name=reviewer_project)).id
    )
    experiment_project = await asyncio.to_thread(client.read_project, project_name=experiment)
    runs: list[Run] = await asyncio.to_thread(
        lambda: list(client.list_runs(project_id=experiment_project.id, is_root=True))
    )
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_LOOKUPS)

    async def _price(run: Run) -> PrCost | str:
        outputs = run.outputs or {}
        pr_url = str(outputs.get("pr_url") or run.id)
        thread_id = outputs.get("thread_id")
        if not isinstance(thread_id, str):
            return pr_url
        async with semaphore:
            priced = await _thread_cost(async_client, reviewer_project_id, thread_id)
        if priced is None:
            return pr_url
        cost_usd, total_tokens = priced
        await asyncio.to_thread(
            _write_feedback, client, experiment_project.id, run.id, "cost_usd", score=cost_usd
        )
        # LangSmith caps feedback scores below 100k, which one review's tokens exceed.
        await asyncio.to_thread(
            _write_feedback,
            client,
            experiment_project.id,
            run.id,
            "total_tokens",
            value=total_tokens,
        )
        return PrCost(run.id, pr_url, thread_id, cost_usd, total_tokens)

    results = await asyncio.gather(*(_price(run) for run in runs))
    summary = CostSummary(
        experiment=experiment,
        priced=[item for item in results if isinstance(item, PrCost)],
        unpriced=[item for item in results if isinstance(item, str)],
    )
    await asyncio.to_thread(
        client.update_project,
        experiment_project.id,
        metadata={
            **(experiment_project.metadata or {}),
            "cost_total_usd": round(summary.total_usd, 4),
            "cost_mean_per_pr_usd": round(summary.mean_usd, 4),
            "cost_median_per_pr_usd": round(summary.median_usd, 4),
            "cost_max_per_pr_usd": round(summary.max_usd, 4),
            "cost_total_tokens": summary.total_tokens,
            "cost_priced_prs": len(summary.priced),
            "cost_unpriced_prs": len(summary.unpriced),
            "reviewer_langsmith_project": reviewer_project,
        },
    )
    return summary


def print_summary(summary: CostSummary) -> None:
    print(f"\nCost for {summary.experiment}")
    for item in sorted(summary.priced, key=lambda item: item.cost_usd, reverse=True):
        print(f"  ${item.cost_usd:8.4f}  {item.total_tokens:>10,} tok  {item.pr_url}")
    for pr_url in summary.unpriced:
        print(f"  {'unpriced':>9}  {'':>14}  {pr_url}")
    print(
        f"  total ${summary.total_usd:.2f} over {len(summary.priced)} PRs | "
        f"mean ${summary.mean_usd:.4f} | median ${summary.median_usd:.4f} | "
        f"max ${summary.max_usd:.4f} | {summary.total_tokens:,} tokens"
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True, help="LangSmith experiment name.")
    ap.add_argument(
        "--reviewer-project",
        default=os.environ.get("LANGSMITH_PROJECT"),
        help="LangSmith project the reviewer server traces into (default: $LANGSMITH_PROJECT).",
    )
    args = ap.parse_args()
    if not args.reviewer_project:
        ap.error("--reviewer-project is required when LANGSMITH_PROJECT is unset")
    print_summary(await record_experiment_costs(args.experiment, args.reviewer_project))


if __name__ == "__main__":
    asyncio.run(main())
