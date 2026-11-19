"""Score Robin runs from their LangSmith traces.

    uv run python -m evals.trajectories.score <trace-id> [<trace-id> ...]
    uv run python -m evals.trajectories.score --thread <thread-id> --since 2026-11-10
    uv run python -m evals.trajectories.score <trace-id> --feedback

Prints one row per trace; ``--feedback`` also records each score on the trace's root run,
under ``trajectory.<score>``, so a dashboard or an experiment can compare them.
"""

import argparse
import json
from collections.abc import Iterator
from datetime import datetime
from uuid import uuid5

from dotenv import load_dotenv
from langsmith import Client
from langsmith.schemas import Run

from evals.trajectories.scoring import Trajectory

PROJECT = "open-swe-mda-prod-production"


class RobinTraces:
    """The traces Robin writes to one LangSmith project."""

    def __init__(self, client: Client, project: str) -> None:
        self.client = client
        self.project = project

    def of_thread(self, thread_id: str, since: datetime | None = None) -> list[str]:
        """The trace of every turn of ``thread_id``."""
        roots = self.client.list_runs(
            project_name=self.project,
            is_root=True,
            filter=f'and(eq(metadata_key, "thread_id"), eq(metadata_value, "{thread_id}"))',
            start_time=since,
        )
        return [str(root.trace_id) for root in roots]

    def steps(self, trace_id: str) -> Iterator[Run]:
        """A trace's model and tool calls; listing a whole trace drops most outputs."""
        for kind in ("llm", "tool"):
            yield from self.client.list_runs(
                project_name=self.project, trace_id=trace_id, run_type=kind
            )

    def score(self, *trace_ids: str) -> dict[str, float | int | bool]:
        """The scores of ``trace_ids`` taken as one trajectory, as a thread's turns are."""
        return Trajectory.of(run for trace in trace_ids for run in self.steps(trace)).scores()

    def record(self, trace_id: str, scores: dict[str, float | int | bool]) -> None:
        """Keep ``scores`` as feedback on the trace's root run, once per key."""
        root = next(self.client.list_runs(project_name=self.project, run_ids=[trace_id]))
        for key, value in scores.items():
            feedback_key = f"trajectory.{key}"
            unanswered = key == "first_reply_seconds" and not scores["replied"]
            self.client.create_feedback(
                run_id=root.id,
                feedback_id=uuid5(root.id, feedback_key),
                key=feedback_key,
                score=None if unanswered else float(value),
                comment="Robin did not reply" if unanswered else None,
            )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("traces", nargs="*", help="trace IDs to score")
    parser.add_argument("--thread", help="score every turn of this Robin thread instead")
    parser.add_argument("--since", help="with --thread, the earliest turn (ISO date)")
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--feedback", action="store_true", help="record scores on each run")
    args = parser.parse_args()

    traces = RobinTraces(Client(), args.project)
    trace_ids: list[str] = list(args.traces)
    if args.thread:
        since = datetime.fromisoformat(args.since) if args.since else None
        trace_ids += traces.of_thread(args.thread, since)
    for trace_id in trace_ids:
        scores = traces.score(trace_id)
        print(json.dumps({"trace": trace_id, **scores}))
        if args.feedback:
            traces.record(trace_id, scores)


if __name__ == "__main__":
    main()
