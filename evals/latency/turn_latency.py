"""How long Robin takes to finish a Slack turn on prod, this week against last week and a baseline.

    uv run --no-project --with langsmith python -m evals.latency.turn_latency --slack <file>

A turn is a root ``open-swe`` run started by a Slack mention, thread reply or DM; its duration is
about how long the person waited for Robin's answer. Prints a summary, and with ``--slack`` writes
it there for the weekly post.
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Self, Unpack

from langsmith import Client
from langsmith.pagination import AsyncItemsCursorPostPagination
from langsmith.types.run import Run
from langsmith.types.run_query_v2_params import RunQueryV2Params

PROJECT_ID = "01a138e6-f2b1-7bfe-9f9c-a0be0877f1b2"  # open-swe-mda-prod-production
SLACK_EVENTS = ("mention", "message", "directMessage")
SLOW_SECONDS = 300
# The workspace shares its run-query rate limit across every API key in it.
RETRY_SECONDS = (15, 30, 60, 60, 120)
# Nov 10–17, 2026: the week before the slow-trajectory fixes (RBN-211).
BASELINE = (82.0, 482.0, 0.17)


async def _page(
    client: Client, **query: Unpack[RunQueryV2Params]
) -> AsyncItemsCursorPostPagination[Run]:
    for wait in RETRY_SECONDS:
        try:
            return await client.runs.query_v2(**query)
        except Exception as exc:
            if "429" not in str(exc) and "rate" not in str(exc).lower():
                raise
            await asyncio.sleep(wait)
    raise RuntimeError("LangSmith kept rate-limiting the run query")


async def turns(client: Client, start: datetime, end: datetime) -> list[float]:
    """Durations of the Slack turns that started in [start, end)."""
    seconds = []
    for event in SLACK_EVENTS:
        query = (
            f'and(eq(name, "open-swe"), eq(metadata_key, "mda_provider_event_type"), '
            f'eq(metadata_value, "{event}"))'
        )
        cursor = None
        while True:
            page = await _page(
                client,
                project_ids=[PROJECT_ID],
                is_root=True,
                filter=query,
                min_start_time=start.isoformat(),
                max_start_time=end.isoformat(),
                selects=["LATENCY_SECONDS"],
                page_size=1000,
                **({"cursor": cursor} if cursor else {}),
            )
            for run in page.items:
                latency = getattr(run, "latency_seconds", None)
                if latency is not None:
                    seconds.append(latency)
            cursor = page.next_cursor
            if not cursor:
                break
    return seconds


@dataclass(frozen=True)
class Week:
    turns: int
    p50: float
    p90: float
    slow: float

    @classmethod
    def of(cls, seconds: list[float]) -> Self | None:
        if not seconds:
            return None
        ordered = sorted(seconds)

        slow = sum(s > SLOW_SECONDS for s in ordered) / len(ordered)
        return cls(len(ordered), median(ordered), ordered[int(0.9 * len(ordered))], slow)

    def line(self, label: str) -> str:
        return (
            f"{label}: median {self.p50:.0f}s, 90th percentile {self.p90:.0f}s, "
            f"{self.slow:.0%} over 5 min ({self.turns} turns)"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slack", type=Path, help="write the summary here")
    args = parser.parse_args()
    client = Client()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    week, previous = now - timedelta(days=7), now - timedelta(days=14)
    this = Week.of(await turns(client, week, now))
    last = Week.of(await turns(client, previous, week))
    lines = [f"*Robin Slack turn latency, {week:%b %d} – {now:%b %d}*"]
    lines.append(this.line("This week") if this else "This week: no Slack turns traced")
    if last:
        lines.append(last.line("Last week"))
    p50, p90, slow = BASELINE
    lines.append(
        f"Baseline (Nov 10–17, before RBN-211): median {p50:.0f}s, 90th percentile {p90:.0f}s, "
        f"{slow:.0%} over 5 min"
    )
    summary = "\n".join(lines)
    print(summary)
    if args.slack is not None:
        args.slack.write_text(summary)


if __name__ == "__main__":
    asyncio.run(main())
