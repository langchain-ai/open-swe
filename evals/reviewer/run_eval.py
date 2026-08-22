"""Run the reviewer eval against the LangSmith dataset.

Usage:
    uv run python -m evals.reviewer.run_eval
"""

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import threading
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langgraph_sdk import get_client
from langsmith import Client, aevaluate
from langsmith.schemas import Example

from agent.review.eval_config import (
    ENV_VARS,
    FIELDS,
    ReviewerEvalConfig,
    coerce_config,
    config_from_env,
    resolve_config,
)
from agent.review.eval_store import EXPERIMENT_URL_RE, LOG_TAIL_CHARS, EvalStatus
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


def _add_config_arguments(ap: argparse.ArgumentParser) -> None:
    for field in FIELDS:
        kwargs: dict[str, Any] = {"dest": field.name, "default": None}
        if field.is_int:
            kwargs["type"] = int
        if field.choices:
            kwargs["choices"] = list(field.choices)
        ap.add_argument(field.cli_flag, **kwargs)


def _load_toml_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as f:
        return coerce_config(tomllib.load(f))


def _config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {name: value for name in ENV_VARS if (value := getattr(args, name, None)) is not None}


def _apply_config_to_env(config: ReviewerEvalConfig) -> None:
    values = dict(config)
    for name, env_var in ENV_VARS.items():
        if name != "langsmith_project":
            os.environ[env_var] = str(values[name])
    _apply_langsmith_project(config["langsmith_project"])


def _apply_langsmith_project(project: str) -> None:
    """Route eval traces to a dedicated LangSmith project."""
    os.environ["LANGSMITH_PROJECT"] = project
    os.environ["LANGCHAIN_PROJECT"] = project
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
            self._buf = (self._buf + text)[-LOG_TAIL_CHARS:]
            found = EXPERIMENT_URL_RE.findall(self._buf)
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
    _add_config_arguments(ap)
    ap.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip deleting LangGraph threads after the experiment finishes.",
    )
    args = ap.parse_args()
    config = resolve_config(_load_toml_config(), config_from_env(), _config_from_args(args))
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
            config=config,
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
            status: EvalStatus = "failed" if eval_error is not None else "completed"
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
