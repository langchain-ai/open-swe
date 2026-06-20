"""Run the reviewer eval against the LangSmith dataset.

Usage:
    uv run python -m evals.reviewer.run_eval
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import threading
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv
from langgraph_sdk import get_client
from langsmith import Client, aevaluate
from langsmith.schemas import Example

from agent.reviewer_eval_store import _EXPERIMENT_URL_RE, _LOG_TAIL_CHARS
from evals.reviewer.judge import aggregate_pr, judge_match
from evals.reviewer.store_reporter import StoreReporter, is_enabled
from evals.reviewer.target import (
    drain_thread_ids,
    get_completed_count,
    get_langgraph_url,
    review_pr,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name("config.toml")
DEFAULT_LANGSMITH_PROJECT = "open-swe-evals"
ScoreMode = Literal["all_findings", "surfaced_findings"]
Severity = Literal["low", "medium", "high", "critical"]
_VALID_SCORE_MODES: set[str] = {"all_findings", "surfaced_findings"}
_VALID_SEVERITIES: set[str] = {"low", "medium", "high", "critical"}

_ENV_MAPPING: dict[str, str] = {
    "dataset_name": "REVIEWER_EVAL_DATASET_NAME",
    "experiment_prefix": "REVIEWER_EVAL_EXPERIMENT_PREFIX",
    "max_concurrency": "REVIEWER_EVAL_MAX_CONCURRENCY",
    "langgraph_url": "LANGGRAPH_URL",
    "langsmith_project": "LANGSMITH_PROJECT",
    "assistant_id": "REVIEWER_ASSISTANT_ID",
    "model_id": "REVIEWER_EVAL_MODEL_ID",
    "reasoning_effort": "REVIEWER_EVAL_REASONING_EFFORT",
    "score_mode": "REVIEWER_EVAL_SCORE_MODE",
    "severity_threshold": "REVIEWER_EVAL_SEVERITY_THRESHOLD",
    "cap": "REVIEWER_EVAL_CAP",
}


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


DEFAULT_CONFIG: ReviewerEvalConfig = {
    "dataset_name": "openswe-reviewer-v1",
    "experiment_prefix": "openswe-reviewer-baseline",
    "max_concurrency": 5,
    "langgraph_url": "",
    "langsmith_project": DEFAULT_LANGSMITH_PROJECT,
    "assistant_id": "reviewer",
    "model_id": "google_genai:gemini-3.5-flash",
    "reasoning_effort": "medium",
    "score_mode": "all_findings",
    "severity_threshold": "medium",
    "cap": 4,
}


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


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
    if score_mode in _VALID_SCORE_MODES:
        config["score_mode"] = score_mode

    severity_threshold = raw.get("severity_threshold")
    if severity_threshold in _VALID_SEVERITIES:
        config["severity_threshold"] = severity_threshold

    cap = raw.get("cap")
    if isinstance(cap, int) and cap >= 0:
        config["cap"] = cap
    return config


def _load_env_config(env: Mapping[str, str] = os.environ) -> ReviewerEvalConfig:
    raw: dict[str, Any] = {}
    for config_key, env_key in _ENV_MAPPING.items():
        value = env.get(env_key)
        if value is None or value == "":
            continue
        if config_key in {"max_concurrency", "cap"}:
            parsed = _parse_int(value)
            if parsed is not None:
                raw[config_key] = parsed
        else:
            raw[config_key] = value
    return _coerce_config(raw)


def _config_from_args(args: argparse.Namespace) -> ReviewerEvalConfig:
    raw: dict[str, Any] = {}
    for key in _ENV_MAPPING:
        value = getattr(args, key, None)
        if value is not None:
            raw[key] = value
    return _coerce_config(raw)


def _resolve_config(cli_config: ReviewerEvalConfig | None = None) -> ReviewerEvalConfig:
    resolved: ReviewerEvalConfig = {
        **DEFAULT_CONFIG,
        **_load_config(),
        **_load_env_config(),
    }
    if cli_config is not None:
        resolved.update(cli_config)
    return resolved


def _apply_config_to_env(config: ReviewerEvalConfig) -> None:
    for config_key, env_key in _ENV_MAPPING.items():
        if config_key == "langsmith_project":
            continue
        value = config.get(config_key)
        if value is not None:
            os.environ[env_key] = str(value)
    _apply_langsmith_project(config.get("langsmith_project"))


def _apply_langsmith_project(project: str | None) -> None:
    """Route eval traces to a dedicated LangSmith project.

    ``project`` is expected to be the resolved value after CLI/env/config
    precedence has already been applied.
    """
    resolved = project or os.environ.get("LANGSMITH_PROJECT") or DEFAULT_LANGSMITH_PROJECT
    os.environ["LANGSMITH_PROJECT"] = resolved
    os.environ["LANGCHAIN_PROJECT"] = resolved
    os.environ.setdefault("LANGSMITH_TRACING", "true")


class _TailCapture:
    """Thread-safe rolling tail of eval output + the last LangSmith experiment URL."""

    def __init__(self) -> None:
        self._buf = ""
        self._url: str | None = None
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._buf = (self._buf + text)[-_LOG_TAIL_CHARS:]
            found = _EXPERIMENT_URL_RE.findall(self._buf)
            if found:
                self._url = found[-1]

    def tail(self) -> str | None:
        with self._lock:
            return self._buf or None

    def url(self) -> str | None:
        with self._lock:
            return self._url


class _BufferingHandler(logging.Handler):
    """Mirror log records into a ``_TailCapture`` so the reporter can publish them."""

    def __init__(self, capture: _TailCapture) -> None:
        super().__init__()
        self._capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._capture.append(self.format(record) + "\n")
        except Exception:
            pass


class _TeeStream:
    """Write to the original stream and mirror into the capture (for ``print``ed output)."""

    def __init__(self, original: Any, capture: _TailCapture) -> None:
        self._original = original
        self._capture = capture

    def write(self, text: str) -> int:
        self._capture.append(text)
        return self._original.write(text)

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def _resolve_total(dataset_name: str, data: str | list[Example]) -> int | None:
    if isinstance(data, list):
        return len(data)
    try:
        return Client().read_dataset(dataset_name=dataset_name).example_count
    except Exception:
        logger.warning("Could not resolve dataset example count for progress", exc_info=True)
        return None


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
    logging.basicConfig(
        level=os.environ.get("REVIEWER_EVAL_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Run only the first N examples.")
    ap.add_argument("--dataset-name", dest="dataset_name")
    ap.add_argument("--experiment-prefix", dest="experiment_prefix")
    ap.add_argument("--max-concurrency", dest="max_concurrency", type=int)
    ap.add_argument("--langgraph-url", dest="langgraph_url")
    ap.add_argument("--langsmith-project", dest="langsmith_project")
    ap.add_argument("--assistant-id", dest="assistant_id")
    ap.add_argument("--model-id", dest="model_id")
    ap.add_argument("--reasoning-effort", dest="reasoning_effort")
    ap.add_argument("--score-mode", dest="score_mode", choices=sorted(_VALID_SCORE_MODES))
    ap.add_argument(
        "--severity-threshold",
        dest="severity_threshold",
        choices=sorted(_VALID_SEVERITIES),
    )
    ap.add_argument("--cap", type=int)
    ap.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip deleting LangGraph threads after the experiment finishes.",
    )
    args = ap.parse_args()
    config = _resolve_config(_config_from_args(args))
    _apply_config_to_env(config)

    dataset_name = config["dataset_name"]
    experiment_prefix = config["experiment_prefix"]
    max_concurrency = config["max_concurrency"]
    logger.info(
        "Starting reviewer eval: dataset=%s experiment_prefix=%s max_concurrency=%s "
        "model=%s effort=%s score_mode=%s severity_threshold=%s cap=%s project=%s "
        "assistant_id=%s langgraph_url=%s limit=%s",
        dataset_name,
        experiment_prefix,
        max_concurrency,
        config["model_id"],
        config["reasoning_effort"],
        config["score_mode"],
        config["severity_threshold"],
        config["cap"],
        config["langsmith_project"],
        config["assistant_id"],
        config["langgraph_url"] or "(default)",
        args.limit,
    )

    data: str | list[Example]
    if args.limit:
        client = Client()
        data = list(client.list_examples(dataset_name=dataset_name, limit=args.limit))
    else:
        data = dataset_name

    reporter: StoreReporter | None = None
    heartbeat: asyncio.Task[None] | None = None
    log_handler: logging.Handler | None = None
    original_stdout = sys.stdout
    if is_enabled():
        capture = _TailCapture()
        log_handler = _BufferingHandler(capture)
        log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(log_handler)
        sys.stdout = _TeeStream(original_stdout, capture)
        reporter = StoreReporter(
            config=dict(config),
            limit=args.limit,
            total=_resolve_total(dataset_name, data),
            created_by=None,
            completed_getter=get_completed_count,
            tail_getter=capture.tail,
            experiment_url_getter=capture.url,
        )
        await reporter.start()
        heartbeat = reporter.run_heartbeat()

    eval_error: BaseException | None = None
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
    except BaseException as exc:
        eval_error = exc
        raise
    finally:
        if reporter is not None:
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            status = "failed" if eval_error is not None else "completed"
            error = None if eval_error is None else f"{type(eval_error).__name__}: {eval_error}"
            await reporter.finish(status=status, error=error)
        if log_handler is not None:
            logging.getLogger().removeHandler(log_handler)
        sys.stdout = original_stdout
        if not args.no_cleanup:
            thread_ids = drain_thread_ids()
            if thread_ids:
                logger.info("Cleaning up %d LangGraph threads", len(thread_ids))
                await _cleanup_threads(thread_ids)


if __name__ == "__main__":
    asyncio.run(main())
