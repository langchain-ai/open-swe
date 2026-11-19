"""Report a Harbor job of Robin replay tasks: each trial's reward and trajectory scores.

    uv run python -m evals.trajectories.report <job-dir> --project <tracing project>

Each trial's agent log names the Robin thread it ran in; its turns are scored together.
Prints a Markdown table, also appended to ``$GITHUB_STEP_SUMMARY`` when that is set.
Without LangSmith credentials only rewards and threads are reported. Exits nonzero when a
trial errored or never reported, so a lost replay fails CI; a low reward does not.
"""

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from langsmith import Client

from evals.trajectories.score import RobinTraces

_THREAD = re.compile(r"^Thread (\S+/agents/([0-9a-f-]{36}))", re.MULTILINE)
_COLUMNS = ("blocking_execute_seconds", "used_background", "slow_eval_seconds", "replied")


@dataclass(frozen=True)
class Trial:
    """One task's run in a Harbor job."""

    task: str
    reward: float | None
    thread_url: str | None
    thread_id: str | None

    @classmethod
    def of(cls, directory: Path) -> Self:
        result = json.loads((directory / "result.json").read_text())
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        log = directory / "agent" / "oswe.txt"
        found = _THREAD.search(log.read_text()) if log.exists() else None
        return cls(
            task=result["task_name"],
            reward=rewards.get("reward"),
            thread_url=found.group(1) if found else None,
            thread_id=found.group(2) if found else None,
        )

    @classmethod
    def in_job(cls, job: Path) -> list[Self]:
        return sorted(
            (cls.of(path.parent) for path in job.glob("*/result.json")),
            key=lambda trial: trial.task,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job", type=Path, help="the Harbor job directory")
    parser.add_argument("--project", required=True, help="where the deployment traces runs")
    args = parser.parse_args()

    traces = RobinTraces(Client(), args.project) if os.environ.get("LANGSMITH_API_KEY") else None
    header = ["task", "reward", "thread", "wall_seconds", *_COLUMNS]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    trials = Trial.in_job(args.job)
    for trial in trials:
        scores: dict[str, float | int | bool] = {}
        if traces is not None and trial.thread_id is not None:
            scores = traces.score(*traces.of_thread(trial.thread_id))
        thread = f"[thread]({trial.thread_url})" if trial.thread_url else "none"
        row = [trial.task, str(trial.reward), thread]
        row += [str(scores.get(column, "")) for column in ("wall_seconds", *_COLUMNS)]
        lines.append("| " + " | ".join(row) + " |")
    table = "\n".join(lines) + "\n"
    print(table)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as out:
            out.write("## Robin replay tasks\n\n" + table)
    job = json.loads((args.job / "result.json").read_text())
    errored = job["stats"]["n_errored_trials"]
    missing = job["n_total_trials"] - len(trials)
    if errored or missing:
        raise SystemExit(f"{errored} trial(s) errored and {missing} never reported")


if __name__ == "__main__":
    main()
