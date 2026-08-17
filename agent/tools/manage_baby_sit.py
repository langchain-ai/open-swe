"""Tool for managing durable `/baby-sit` PR watches."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from langgraph.config import get_config

from ..baby_sit import record_retry, start_watch, stop_watch, watch_key
from ..utils.auth import resolve_github_token
from ..utils.github_app import get_github_app_installation_id_for_repo
from ..utils.github_ci import fetch_pr
from ..utils.slack import parse_github_pr_url


def _configurable() -> tuple[dict[str, Any], Mapping[str, Any]]:
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    return dict(configurable) if isinstance(configurable, Mapping) else {}, config


def _matches_configured_repo(configurable: dict[str, Any], owner: str, repo: str) -> bool:
    configured = configurable.get("repo")
    if not isinstance(configured, Mapping):
        return True
    configured_owner = configured.get("owner")
    configured_repo = configured.get("name")
    if not isinstance(configured_owner, str) or not isinstance(configured_repo, str):
        return False
    return configured_owner.lower() == owner.lower() and configured_repo.lower() == repo.lower()


def _run_config(configurable: dict[str, Any], thread_id: str) -> dict[str, Any]:
    allowed = (
        "source",
        "slack_thread",
        "linear_issue",
        "github_issue",
        "pr_number",
        "github_login",
        "user_email",
        "environment",
        "agent_model_id",
        "agent_effort",
    )
    result = {key: configurable[key] for key in allowed if configurable.get(key) is not None}
    result["thread_id"] = thread_id
    return result


def _source_context(configurable: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in ("slack_thread", "linear_issue", "github_issue"):
        value = configurable.get(key)
        if isinstance(value, Mapping):
            context[key] = dict(value)
    return context


async def manage_baby_sit(
    pr_url: str,
    action: Literal["start", "stop", "record_retry"] = "start",
    head_sha: str = "",
    check_name: str = "",
    evidence: str = "",
    details_url: str = "",
) -> dict[str, Any]:
    """Start, stop, or record a flaky rerun for a `/baby-sit` PR watch."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return {"success": False, "error": "pr_url must be a canonical GitHub pull request URL"}

    configurable, config = _configurable()
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No executable agent thread is available"}
    if not _matches_configured_repo(configurable, pr_ref.owner, pr_ref.repo):
        return {"success": False, "error": "Pull request does not match this thread's repository"}

    key = watch_key(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if action == "stop":
        from ..baby_sit import get_watch

        watch = await get_watch(key)
        if watch and watch.get("thread_id") != thread_id:
            return {"success": False, "error": "This watch belongs to another agent thread"}
        stopped = await stop_watch(key)
        return {"success": True, "stopped": stopped, "watch_key": key}

    if action == "record_retry":
        if not head_sha.strip() or not check_name.strip() or not evidence.strip():
            return {
                "success": False,
                "error": "head_sha, check_name, and evidence are required when recording a flaky rerun",
            }
        return await record_retry(
            key,
            thread_id=thread_id,
            head_sha=head_sha.strip(),
            check_name=check_name,
            evidence=evidence,
            details_url=details_url,
        )

    try:
        token, _ = await resolve_github_token(config, thread_id)
    except Exception as exc:
        return {"success": False, "error": f"GitHub authentication failed: {exc}"}
    pr = await fetch_pr(
        owner=pr_ref.owner,
        repo=pr_ref.repo,
        pr_number=pr_ref.number,
        token=token,
    )
    if not pr:
        return {"success": False, "error": "Pull request is unavailable"}
    if pr.get("state") != "open":
        return {"success": False, "error": "Pull request is not open"}
    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    pr_head_sha = head.get("sha") if isinstance(head, Mapping) else None
    pr_head_ref = head.get("ref") if isinstance(head, Mapping) else None
    if not isinstance(pr_head_sha, str) or not pr_head_sha:
        return {"success": False, "error": "Pull request head SHA is unavailable"}
    if not isinstance(pr_head_ref, str) or not pr_head_ref:
        return {"success": False, "error": "Pull request head branch is unavailable"}

    installation_id = await get_github_app_installation_id_for_repo(pr_ref.owner, pr_ref.repo)
    if installation_id is None:
        return {
            "success": False,
            "error": "GitHub App installation is unavailable for this repository",
        }
    try:
        watch = await start_watch(
            pr_ref=pr_ref,
            head_sha=pr_head_sha,
            head_ref=pr_head_ref,
            installation_id=installation_id,
            thread_id=thread_id,
            run_config=_run_config(configurable, thread_id),
            source_context=_source_context(configurable),
        )
    except Exception as exc:
        return {"success": False, "error": f"Could not start baby-sit watch: {exc}"}
    return {
        "success": True,
        "watch_key": watch["key"],
        "pr_url": watch["pr_url"],
        "head_sha": watch["head_sha"],
        "poll_schedule": "every 10 minutes",
        "webhook_first": True,
    }
