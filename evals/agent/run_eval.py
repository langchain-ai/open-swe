"""Run the agent eval against the LangSmith dataset.

Usage:
    uv run python -m evals.agent.run_eval [--limit 3]
"""

import argparse
import asyncio
import logging
import os
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langgraph_sdk import get_client
from langsmith import Client, aevaluate
from langsmith.schemas import Example
from pydantic import TypeAdapter

from evals.agent.judge import aggregate_resolution, get_results, judge_resolution, reset_results
from evals.agent.report import write_report
from evals.agent.target import (
    drain_thread_ids,
    get_agent_assistant_id,
    get_langgraph_url,
    solve_task,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name("config.toml")
DEFAULT_LANGSMITH_PROJECT = "open-swe-evals"

_ENV_MAPPING: dict[str, str] = {
    "dataset_name": "AGENT_EVAL_DATASET_NAME",
    "experiment_prefix": "AGENT_EVAL_EXPERIMENT_PREFIX",
    "max_concurrency": "AGENT_EVAL_MAX_CONCURRENCY",
    "langgraph_url": "LANGGRAPH_URL",
    "langsmith_project": "LANGSMITH_PROJECT",
    "assistant_id": "AGENT_ASSISTANT_ID",
    "model_id": "AGENT_EVAL_MODEL_ID",
    "reasoning_effort": "AGENT_EVAL_REASONING_EFFORT",
    "run_source": "AGENT_EVAL_RUN_SOURCE",
    "local_project_dir": "AGENT_EVAL_LOCAL_PROJECT_DIR",
    "test_timeout_seconds": "AGENT_EVAL_TEST_TIMEOUT_SECONDS",
    "compaction_trigger_tokens": "AGENT_EVAL_COMPACTION_TRIGGER_TOKENS",
    "compaction_trigger_messages": "AGENT_EVAL_COMPACTION_TRIGGER_MESSAGES",
    "compaction_trigger_fraction": "AGENT_EVAL_COMPACTION_TRIGGER_FRACTION",
    "compaction_summary_prompt": "AGENT_EVAL_COMPACTION_SUMMARY_PROMPT",
}


class AgentEvalConfig(TypedDict, total=False):
    dataset_name: str
    experiment_prefix: str
    max_concurrency: int
    langgraph_url: str
    langsmith_project: str
    assistant_id: str
    model_id: str
    reasoning_effort: str
    run_source: str
    local_project_dir: str
    test_timeout_seconds: int
    compaction_trigger_tokens: int
    compaction_trigger_messages: int
    compaction_trigger_fraction: float
    compaction_summary_prompt: str


DEFAULT_CONFIG: AgentEvalConfig = {
    "dataset_name": "openswe-agent-v1",
    "experiment_prefix": "openswe-agent-baseline",
    "max_concurrency": 4,
    "langgraph_url": "",
    "langsmith_project": DEFAULT_LANGSMITH_PROJECT,
    "assistant_id": "agent",
    "model_id": "anthropic:claude-opus-5",
    "reasoning_effort": "medium",
    "run_source": "eval",
    "local_project_dir": "",
    "test_timeout_seconds": 1800,
}

_INT_KEYS = {
    "max_concurrency",
    "test_timeout_seconds",
    "compaction_trigger_tokens",
    "compaction_trigger_messages",
}
_FLOAT_KEYS = {"compaction_trigger_fraction"}
_CONFIG_ADAPTER = TypeAdapter(AgentEvalConfig)


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _coerce_config(raw: Mapping[str, object]) -> AgentEvalConfig:
    config: dict[str, str | int | float] = {}
    for key in _ENV_MAPPING:
        value = raw.get(key)
        if value is None or value == "":
            continue
        if key in _INT_KEYS:
            parsed = _parse_int(str(value))
            if parsed is not None and parsed > 0:
                config[key] = parsed
        elif key in _FLOAT_KEYS:
            parsed = _parse_float(str(value))
            if parsed is not None and 0 < parsed <= 1:
                config[key] = parsed
        elif isinstance(value, str):
            config[key] = value
    return _CONFIG_ADAPTER.validate_python(config)


def _load_config() -> AgentEvalConfig:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as handle:
        return _coerce_config(tomllib.load(handle))


def _load_env_config(env: Mapping[str, str] = os.environ) -> AgentEvalConfig:
    raw: dict[str, object] = {}
    for config_key, env_key in _ENV_MAPPING.items():
        value = env.get(env_key)
        if value is not None and value != "":
            raw[config_key] = value
    return _coerce_config(raw)


def _config_from_args(args: argparse.Namespace) -> AgentEvalConfig:
    raw: dict[str, object] = {}
    for key in _ENV_MAPPING:
        value = getattr(args, key, None)
        if value is not None:
            raw[key] = value
    return _coerce_config(raw)


def _resolve_config(cli_config: AgentEvalConfig | None = None) -> AgentEvalConfig:
    resolved: AgentEvalConfig = {
        **DEFAULT_CONFIG,
        **_load_config(),
        **_load_env_config(),
    }
    if cli_config is not None:
        resolved.update(cli_config)
    return resolved


def _apply_config_to_env(config: AgentEvalConfig) -> None:
    for config_key, env_key in _ENV_MAPPING.items():
        if config_key == "langsmith_project":
            continue
        value = config.get(config_key)  # type: ignore[arg-type]
        if value is None:
            continue
        if isinstance(value, str) and not value:
            # An unset option must not shadow the target's own default; an
            # empty LANGGRAPH_URL used to produce a client URL with no scheme.
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = str(value)
    project = config.get("langsmith_project") or os.environ.get("LANGSMITH_PROJECT")
    os.environ["LANGSMITH_PROJECT"] = project or DEFAULT_LANGSMITH_PROJECT
    os.environ.setdefault("LANGSMITH_TRACING", "true")


async def _cleanup_threads(thread_ids: Iterable[str]) -> None:
    """Delete LangGraph threads created during the eval.

    Underlying sandboxes are reclaimed by the provider's TTL — this only
    drops the LangGraph checkpoint/metadata records.
    """
    sdk = get_client(url=get_langgraph_url())
    for thread_id in thread_ids:
        try:
            await sdk.threads.delete(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to delete thread %s: %s", thread_id, exc)


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("AGENT_EVAL_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N examples.")
    parser.add_argument("--dataset-name", dest="dataset_name")
    parser.add_argument("--experiment-prefix", dest="experiment_prefix")
    parser.add_argument("--max-concurrency", dest="max_concurrency", type=int)
    parser.add_argument("--langgraph-url", dest="langgraph_url")
    parser.add_argument("--langsmith-project", dest="langsmith_project")
    parser.add_argument("--assistant-id", dest="assistant_id")
    parser.add_argument("--model-id", dest="model_id")
    parser.add_argument("--reasoning-effort", dest="reasoning_effort")
    parser.add_argument("--run-source", dest="run_source", choices=["eval", "desktop"])
    parser.add_argument("--local-project-dir", dest="local_project_dir")
    parser.add_argument("--compaction-trigger-tokens", dest="compaction_trigger_tokens", type=int)
    parser.add_argument(
        "--compaction-trigger-messages", dest="compaction_trigger_messages", type=int
    )
    parser.add_argument(
        "--compaction-trigger-fraction", dest="compaction_trigger_fraction", type=float
    )
    parser.add_argument(
        "--compaction-summary-prompt",
        dest="compaction_summary_prompt",
        choices=["default", "structured"],
    )
    parser.add_argument("--test-timeout-seconds", dest="test_timeout_seconds", type=int)
    parser.add_argument(
        "--output-dir", type=Path, help="Save JSONL, summary JSON, and Markdown reports."
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip deleting LangGraph threads after the experiment finishes.",
    )
    args = parser.parse_args()
    config = _resolve_config(_config_from_args(args))
    _apply_config_to_env(config)

    dataset_name = config["dataset_name"]
    logger.info(
        "Starting agent eval: dataset=%s experiment_prefix=%s max_concurrency=%s model=%s "
        "effort=%s run_source=%s project=%s assistant_id=%s langgraph_url=%s limit=%s",
        dataset_name,
        config["experiment_prefix"],
        config["max_concurrency"],
        config["model_id"],
        config["reasoning_effort"],
        config["run_source"],
        config["langsmith_project"],
        get_agent_assistant_id(),
        config["langgraph_url"] or "(default)",
        args.limit,
    )

    data: str | list[Example]
    if args.limit:
        data = list(Client().list_examples(dataset_name=dataset_name, limit=args.limit))
    else:
        data = dataset_name

    eval_error: BaseException | None = None
    expected_instances: int | None = None
    reset_results()
    try:
        experiment = await aevaluate(
            solve_task,
            data=data,
            evaluators=[judge_resolution],
            summary_evaluators=[aggregate_resolution],
            experiment_prefix=config["experiment_prefix"],
            max_concurrency=config["max_concurrency"],
            num_repetitions=1,
        )
        expected_instances = len(experiment)
        if len(get_results()) != expected_instances:
            raise RuntimeError("Some experiment runs have no evaluation result")
    except BaseException as exc:
        eval_error = exc
        raise
    finally:
        report_error: Exception | None = None
        if args.output_dir is not None:
            try:
                await write_report(
                    args.output_dir,
                    get_results(),
                    config=dict(config),
                    completed=eval_error is None,
                    expected_instances=expected_instances,
                )
            except Exception as exc:
                logger.exception("Failed to save agent evaluation report")
                report_error = exc
        if not args.no_cleanup:
            thread_ids = drain_thread_ids()
            if thread_ids:
                logger.info("Cleaning up %d LangGraph threads", len(thread_ids))
                await _cleanup_threads(thread_ids)
        if eval_error is not None:
            logger.error("Agent eval failed", extra={"error_type": type(eval_error).__name__})
        elif report_error is not None:
            raise report_error


if __name__ == "__main__":
    asyncio.run(main())
