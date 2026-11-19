"""Score Robin runs from their LangSmith traces.

    uv run python -m evals.trajectories.score <trace-id> [<trace-id> ...]
    uv run python -m evals.trajectories.score --thread <thread-id> --since 2026-11-10
    uv run python -m evals.trajectories.score <trace-id> --feedback

Prints one row per trace; ``--feedback`` also records each score on the trace's root run,
under ``trajectory.<score>``, so a dashboard or an experiment can compare them.
"""

import argparse
import json
from datetime import datetime
from uuid import uuid5

from dotenv import load_dotenv
from langsmith import Client

from evals.trajectories.scoring import Trajectory

PROJECT = "open-swe-mda-prod-production"


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("traces", nargs="*", help="trace IDs to score")
    parser.add_argument("--thread", help="score every turn of this Robin thread instead")
    parser.add_argument("--since", help="with --thread, the earliest turn (ISO date)")
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--feedback", action="store_true", help="record scores on each run")
    args = parser.parse_args()

    client = Client()
    traces: list[str] = list(args.traces)
    if args.thread:
        roots = client.list_runs(
            project_name=args.project,
            is_root=True,
            filter=f'and(eq(metadata_key, "thread_id"), eq(metadata_value, "{args.thread}"))',
            start_time=datetime.fromisoformat(args.since) if args.since else None,
        )
        traces += [str(root.trace_id) for root in roots]
    for trace_id in traces:
        # Only model and tool calls are scored; listing a whole trace drops most outputs.
        runs = [
            run
            for kind in ("llm", "tool")
            for run in client.list_runs(project_name=args.project, trace_id=trace_id, run_type=kind)
        ]
        scores = Trajectory.of(runs).scores()
        print(json.dumps({"trace": trace_id, **scores}))
        if args.feedback:
            root = next(client.list_runs(project_name=args.project, run_ids=[trace_id]))
            for key, value in scores.items():
                feedback_key = f"trajectory.{key}"
                unanswered = key == "first_reply_seconds" and not scores["replied"]
                client.create_feedback(
                    run_id=root.id,
                    feedback_id=uuid5(root.id, feedback_key),
                    key=feedback_key,
                    score=None if unanswered else float(value),
                    comment="Robin did not reply" if unanswered else None,
                )


if __name__ == "__main__":
    main()
