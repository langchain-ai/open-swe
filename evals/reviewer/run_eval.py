"""Run the reviewer eval against the LangSmith dataset.

Usage:
    uv run python -m evals.reviewer.run_eval \\
        --dataset-name openswe-reviewer-v1 \\
        --experiment-prefix openswe-reviewer-baseline \\
        --max-concurrency 5
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv
from langsmith import aevaluate

from evals.reviewer.judge import aggregate_pr, judge_match
from evals.reviewer.target import review_pr

load_dotenv()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-name", default="openswe-reviewer-v1")
    ap.add_argument("--experiment-prefix", default="openswe-reviewer-baseline")
    ap.add_argument("--max-concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None, help="Run only the first N examples.")
    args = ap.parse_args()

    await aevaluate(
        review_pr,
        data=args.dataset_name,
        evaluators=[judge_match],
        summary_evaluators=[aggregate_pr],
        experiment_prefix=args.experiment_prefix,
        max_concurrency=args.max_concurrency,
        num_repetitions=1,
        **({"max_examples": args.limit} if args.limit else {}),
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
