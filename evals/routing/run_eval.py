"""Run the routing eval against the LangSmith dataset.

Usage:
    uv run python -m evals.routing.run_eval --limit 3
"""

import argparse
import asyncio
import logging
import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from langgraph_sdk import get_client
from langsmith import Client, aevaluate
from langsmith.schemas import Example
from pydantic import BaseModel

from evals.routing.judge import (
    aggregate,
    exploration_cost,
    over_routed,
    route_distance,
    route_match,
    title_format,
    title_quality,
    under_routed,
)
from evals.routing.target import drain_thread_ids, get_langgraph_url, route_task

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name("config.toml")


class RoutingEvalConfig(BaseModel):
    dataset_name: str = "openswe-routing-v1"
    experiment_prefix: str = "openswe-routing"
    max_concurrency: int = 3
    langgraph_url: str = ""
    langsmith_project: str = "open-swe-evals"
    assistant_id: str = "agent"
    github_login: str = "routing-eval"
    timeout_seconds: int = 600


_ENV = {
    "langgraph_url": "LANGGRAPH_URL",
    "langsmith_project": "LANGSMITH_PROJECT",
    "assistant_id": "ROUTING_EVAL_ASSISTANT_ID",
    "github_login": "ROUTING_EVAL_GITHUB_LOGIN",
    "timeout_seconds": "ROUTING_EVAL_TIMEOUT_SECONDS",
}


def _load_config(args: argparse.Namespace) -> RoutingEvalConfig:
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    for key in RoutingEvalConfig.model_fields:
        value = getattr(args, key, None)
        if value not in (None, ""):
            raw[key] = value
    return RoutingEvalConfig.model_validate(raw)


def _export_env(config: RoutingEvalConfig) -> None:
    for key, env_key in _ENV.items():
        value = getattr(config, key)
        if value not in (None, ""):
            os.environ[env_key] = str(value)


async def _cleanup(thread_ids: set[str]) -> None:
    client = get_client(url=get_langgraph_url())
    for thread_id in thread_ids:
        try:
            await client.threads.delete(thread_id)
        except Exception:  # noqa: BLE001
            logger.warning("Could not delete eval thread", extra={"thread": thread_id})


async def main() -> None:
    logging.basicConfig(level=os.environ.get("ROUTING_EVAL_LOG_LEVEL", "INFO"))
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N examples.")
    for key in RoutingEvalConfig.model_fields:
        parser.add_argument(f"--{key.replace('_', '-')}", dest=key)
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()
    config = _load_config(args)
    _export_env(config)
    logger.info("Starting routing eval", extra={"config": config.model_dump(), "limit": args.limit})

    data: str | list[Example] = config.dataset_name
    if args.limit:
        data = list(Client().list_examples(dataset_name=config.dataset_name, limit=args.limit))
    try:
        await aevaluate(
            route_task,
            data=data,
            evaluators=[
                route_match,
                route_distance,
                over_routed,
                under_routed,
                exploration_cost,
                title_format,
                title_quality,
            ],
            summary_evaluators=[aggregate],
            experiment_prefix=config.experiment_prefix,
            max_concurrency=config.max_concurrency,
        )
    finally:
        if not args.no_cleanup:
            await _cleanup(drain_thread_ids())


if __name__ == "__main__":
    asyncio.run(main())
