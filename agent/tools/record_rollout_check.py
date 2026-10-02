"""Store the rollout check on the thread that is opening a pull request."""

from collections.abc import Mapping
from typing import Annotated, Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from langgraph.config import get_config
from langgraph_sdk import get_client
from pydantic import Field

from agent.rollouts import normalize_stages, rollout_repo_allowed
from agent.run_config import RunConfig
from agent.source_context import SourceContext
from agent.tools.manage_baby_sit import dispatch_run_config

_PAGE_LIMIT = 300
_TEXT_LIMIT = 1000


def _clip(value: str, limit: int) -> str:
    return value.strip()[:limit]


def _without_userinfo(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.username and not parsed.password:
        return value
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=host))


async def record_rollout_check(
    environments: Annotated[
        list[dict[str, Any]] | str | None,
        Field(
            description=(
                "Deploy environments for this repository, earliest first. "
                "Each object has name, targets, and datadog_tags."
            )
        ),
    ] = None,
    page: str = "",
    expected: str = "",
    metrics: str = "",
    owner: str = "",
    repo: str = "",
) -> dict[str, Any]:
    """Implement the `record_rollout_check` tool."""
    cfg = RunConfig.from_config(get_config())
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "No executable agent thread is available"}
    repo_owner = owner.strip() or (cfg.repo.owner if cfg.repo else "")
    repo_name = repo.strip() or (cfg.repo.name if cfg.repo else "")
    if not rollout_repo_allowed(repo_owner, repo_name):
        return {"success": True, "watched": False, "reason": "This repository is not watched"}
    stages = normalize_stages(environments)
    if not stages:
        return {
            "success": True,
            "watched": False,
            "reason": "No rollout environments were recorded",
        }
    dumped = cfg.dump()
    source = SourceContext.parse(
        {
            key: dumped[key]
            for key in ("slack_thread", "linear_issue", "github_issue")
            if isinstance(dumped.get(key), Mapping)
        }
    )
    check = {
        "check_id": uuid4().hex,
        "page": _without_userinfo(_clip(page, _PAGE_LIMIT)),
        "expected": _clip(expected, _TEXT_LIMIT),
        "metrics": _clip(metrics, _TEXT_LIMIT),
        "stages": [stage.model_dump() for stage in stages],
        "author": (cfg.github_login or "").strip(),
        "run_config": dispatch_run_config(cfg, thread_id, None),
        "source_context": source.dump(),
    }
    try:
        await get_client().threads.update(
            thread_id=thread_id,
            metadata={"rollout_check": check, "rollout_status": None},
        )
    except Exception:
        return {"success": False, "error": "Could not store the rollout check"}
    return {
        "success": True,
        "watched": True,
        "environments": [stage.model_dump() for stage in stages],
        "page": bool(check["page"]),
        "metrics": bool(check["metrics"]),
    }
