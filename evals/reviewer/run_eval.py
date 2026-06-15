"""Run the reviewer eval against the LangSmith dataset.

Usage:
    uv run python -m evals.reviewer.run_eval
"""

from __future__ import annotations

import argparse
import logging
import os
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv
from langgraph_sdk import get_client
from langsmith import Client, aevaluate
from langsmith.schemas import Example

from evals.reviewer.judge import aggregate_pr, judge_match
from evals.reviewer.target import drain_thread_ids, get_langgraph_url, review_pr

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name("config.toml")
DEFAULT_LANGSMITH_PROJECT = "open-swe-evals"
ScoreMode = Literal["all_findings", "surfaced_findings"]
Severity = Literal["low", "medium", "high", "critical"]


class ReviewerEvalConfig(TypedDict, total=False):
    dataset_name: str
    experiment_prefix: str
    max_concurrency: int
    langgraph_url: str
    langsmith_project: str
    assistant_id: str
    model_id: str
    reasoning_effort: str
    score_mode: ScoreMode
    severity_threshold: Severity
    cap: int


def _load_config() -> ReviewerEvalConfig:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as f:
        raw = tomllib.load(f)
    return _coerce_config(raw)


def _coerce_config(raw: dict[str, Any]) -> ReviewerEvalConfig:
    config: ReviewerEvalConfig = {}
    dataset_name = raw.get("dataset_name")
    if isinstance(dataset_name, str) and dataset_name:
        config["dataset_name"] = dataset_name

    experiment_prefix = raw.get("experiment_prefix")
    if isinstance(experiment_prefix, str) and experiment_prefix:
        config["experiment_prefix"] = experiment_prefix

    langgraph_url = raw.get("langgraph_url")
    if isinstance(langgraph_url, str) and langgraph_url:
        config["langgraph_url"] = langgraph_url

    langsmith_project = raw.get("langsmith_project")
    if isinstance(langsmith_project, str) and langsmith_project:
        config["langsmith_project"] = langsmith_project

    assistant_id = raw.get("assistant_id")
    if isinstance(assistant_id, str) and assistant_id:
        config["assistant_id"] = assistant_id

    model_id = raw.get("model_id")
    if isinstance(model_id, str) and model_id:
        config["model_id"] = model_id

    reasoning_effort = raw.get("reasoning_effort")
    if isinstance(reasoning_effort, str) and reasoning_effort:
        config["reasoning_effort"] = reasoning_effort

    max_concurrency = raw.get("max_concurrency")
    if isinstance(max_concurrency, int) and max_concurrency > 0:
        config["max_concurrency"] = max_concurrency

    score_mode = raw.get("score_mode")
    if score_mode in {"all_findings", "surfaced_findings"}:
        config["score_mode"] = score_mode

    severity_threshold = raw.get("severity_threshold")
    if severity_threshold in {"low", "medium", "high", "critical"}:
        config["severity_threshold"] = severity_threshold

    cap = raw.get("cap")
    if isinstance(cap, int) and cap >= 0:
        config["cap"] = cap
    return config


def _apply_config_to_env(config: ReviewerEvalConfig) -> None:
    env_mapping = {
        "langgraph_url": "LANGGRAPH_URL",
        "assistant_id": "REVIEWER_ASSISTANT_ID",
        "model_id": "REVIEWER_EVAL_MODEL_ID",
        "reasoning_effort": "REVIEWER_EVAL_REASONING_EFFORT",
        "score_mode": "REVIEWER_EVAL_SCORE_MODE",
        "severity_threshold": "REVIEWER_EVAL_SEVERITY_THRESHOLD",
        "cap": "REVIEWER_EVAL_CAP",
    }
    for config_key, env_key in env_mapping.items():
        value = config.get(config_key)
        if value is not None:
            os.environ[env_key] = str(value)
    _apply_langsmith_project(config.get("langsmith_project"))


def _apply_langsmith_project(project: str | None) -> None:
    """Route eval traces to a dedicated LangSmith project.

    A project already set in the environment (e.g. by the admin-triggered job)
    wins so callers can override the config default.
    """
    resolved = os.environ.get("LANGSMITH_PROJECT") or project or DEFAULT_LANGSMITH_PROJECT
    os.environ["LANGSMITH_PROJECT"] = resolved
    os.environ["LANGCHAIN_PROJECT"] = resolved
    os.environ.setdefault("LANGSMITH_TRACING", "true")


async def _cleanup_threads(thread_ids: Iterable[str]) -> None:
    """Delete LangGraph threads created during the eval.

    Underlying sandboxes are reclaimed by the provider's TTL — this only
    drops the LangGraph checkpoint/metadata records.
    """
    sdk = get_client(url=get_langgraph_url())
    for tid in thread_ids:
        try:
            await sdk.threads.delete(tid)
        except Exception as exc:
            logger.warning("Failed to delete thread %s: %s", tid, exc)


async def main() -> None:
    load_dotenv()
    config = _load_config()
    _apply_config_to_env(config)

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Run only the first N examples.")
    ap.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip deleting LangGraph threads after the experiment finishes.",
    )
    args = ap.parse_args()

    dataset_name = config.get("dataset_name", "openswe-reviewer-v1")
    experiment_prefix = config.get("experiment_prefix", "openswe-reviewer-baseline")
    max_concurrency = config.get("max_concurrency", 5)

    data: str | list[Example]
    if args.limit:
        client = Client()
        data = list(client.list_examples(dataset_name=dataset_name, limit=args.limit))
    else:
        data = dataset_name

    try:
        await aevaluate(
            review_pr,
            data=data,
            evaluators=[judge_match],
            summary_evaluators=[aggregate_pr],
            experiment_prefix=experiment_prefix,
            max_concurrency=max_concurrency,
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
