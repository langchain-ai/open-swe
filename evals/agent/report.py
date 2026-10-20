"""Save experiment metrics and complete failure evidence alongside LangSmith."""

import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from agent.prompts import render_prompt
from evals.agent.judge import InstanceResult, summarize_results
from evals.agent.models import FailureStage


async def write_report(
    directory: Path,
    records: list[InstanceResult],
    *,
    config: Mapping[str, object],
    completed: bool,
    expected_instances: int | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    completed = completed and (expected_instances is None or expected_instances == len(records))
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "completed": completed,
        "evaluated_instances": len(records),
        "expected_instances": expected_instances,
        "config": dict(config),
        "metrics": summarize_results(records),
        "failure_counts": dict(
            Counter(
                record.get("failure_stage", FailureStage.NONE)
                for record in records
                if record.get("failure_stage", FailureStage.NONE) != FailureStage.NONE
            )
        ),
        "usage_scope": "main_agent_messages",
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "results.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    metrics = summarize_results(records)
    rows = "\n".join(f"| {key} | {value:.4f} |" for key, value in metrics.items())
    instance_rows = "\n".join(
        f"| {record['instance_id'].replace('|', '/').replace(chr(10), ' ')} "
        f"| {record['resolved']} | {record.get('failure_stage', FailureStage.NONE)} "
        f"| {record['steps']} | {record['wall_clock_seconds']:.3f} |"
        for record in records
    )
    (directory / "report.md").write_text(
        render_prompt(
            "evals/agent-report.md",
            completed=str(completed).lower(),
            evaluated_instances=len(records),
            configuration=json.dumps(dict(config), ensure_ascii=False, indent=2),
            metric_rows=rows,
            instance_rows=instance_rows,
        )
        + "\n",
        encoding="utf-8",
    )
