"""Enforce the saved environment scope of Slack-bot system threads."""

from collections.abc import Mapping
from typing import Any

import httpx2
import langgraph_sdk

from agent.dashboard.environments import ENVIRONMENTS, Environment
from agent.github.app import get_github_app_installation_token_with_expiry
from agent.run_config import RunConfig
from agent.source_context import SourceContext
from agent.utils.http import DEFAULT_HTTP_TIMEOUT
from agent.utils.json_types import thread_metadata


class SystemScopeError(RuntimeError):
    """The saved system authorization cannot grant credentials."""


def repository_scopes_match(left: object, right: object) -> bool:
    if not isinstance(left, list) or not isinstance(right, list) or not left or not right:
        return False
    if not all(isinstance(repo, str) for repo in [*left, *right]):
        return False
    return {repo.lower() for repo in left} == {repo.lower() for repo in right}


async def system_repository_scope(thread_id: str) -> list[str] | None:
    metadata = thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
    return await validate_system_scope(metadata)


async def system_configurable(thread_id: str, configurable: Mapping[str, Any]) -> dict[str, Any]:
    metadata = thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
    cfg = RunConfig.parse(configurable)
    requested_bot = cfg.slack_thread
    if requested_bot and requested_bot.triggering_bot_id:
        opening = SourceContext.from_metadata(metadata).slack_thread
        if (
            metadata.get("owner_type") != "system"
            or opening is None
            or opening.team_id != requested_bot.team_id
            or opening.triggering_bot_id != requested_bot.triggering_bot_id
        ):
            raise SystemScopeError("Slack bot cannot run in a thread with another owner.")
    result = dict(configurable)
    if await validate_system_scope(metadata) is not None:
        result["environment"] = metadata["environment"]
        result["system_environment_thread"] = True
        for key in ("github_login", "github_user_id", "user_email", "admin_thread"):
            result.pop(key, None)
    return result


async def system_environment(thread_id: str) -> Environment | None:
    metadata = thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
    return await _system_environment(metadata)


async def validate_system_scope(metadata: dict[str, Any]) -> list[str] | None:
    environment = await _system_environment(metadata)
    return sorted({repo.lower() for repo in environment.repos}) if environment is not None else None


async def _system_environment(metadata: dict[str, Any]) -> Environment | None:
    slack = SourceContext.from_metadata(metadata).slack_thread
    bot_trigger = slack is not None and bool(slack.triggering_bot_id)
    if not bot_trigger and "system_repositories" not in metadata:
        return None
    if not bot_trigger:
        raise SystemScopeError("System environment has lost its Slack bot authorization.")
    if metadata.get("owner_type") != "system" or metadata.get("visibility") != "public":
        raise SystemScopeError(
            "Slack bots require a public system thread. Start a new Slack thread."
        )
    slug = metadata.get("environment")
    repos = metadata.get("system_repositories")
    if not isinstance(slug, str) or not slug or not isinstance(repos, list) or not repos:
        raise SystemScopeError("System thread has no environment repository scope.")
    if not all(isinstance(repo, str) and repo.count("/") == 1 for repo in repos):
        raise SystemScopeError("System thread has an invalid repository scope.")
    if bot_trigger and slack is not None:
        from agent.slack.allowed_bots import resolve_allowed_slack_bot

        bot = await resolve_allowed_slack_bot(
            slack.team_id,
            slack.triggering_bot_id,
            user_id=slack.triggering_user_id,
            app_id=slack.triggering_bot_app_id,
        )
        if bot is None or bot.environment != slug:
            raise SystemScopeError(
                "This Slack bot is no longer authorized for the thread's environment."
            )
    environment = await ENVIRONMENTS.get(slug)
    if environment is None or not repository_scopes_match(environment.repos, repos):
        raise SystemScopeError(
            "The environment's repository scope changed. Start a new Slack thread."
        )
    return environment


async def system_installation_token(thread_id: str) -> tuple[str, str | None] | None:
    """Return a scoped token, or None for threads without an environment policy.

    Resolve full repository names through the installation before minting by ID;
    passing only short names would lose the organization part of the allowlist.
    """
    repos = await system_repository_scope(thread_id)
    if repos is None:
        return None
    lookup_token, _ = await get_github_app_installation_token_with_expiry()
    if not lookup_token:
        raise SystemScopeError("The GitHub App is unavailable for this system thread.")
    remaining = set(repos)
    ids: list[int] = []
    async with httpx2.AsyncClient(
        timeout=DEFAULT_HTTP_TIMEOUT,
        headers={
            "Authorization": f"Bearer {lookup_token}",
            "Accept": "application/vnd.github+json",
        },
    ) as client:
        page = 1
        while remaining:
            response = await client.get(
                "https://api.github.com/installation/repositories",
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            entries = response.json().get("repositories", [])
            for entry in entries:
                name = str(entry.get("full_name", "")).lower()
                repo_id = entry.get("id")
                if name in remaining and isinstance(repo_id, int) and not isinstance(repo_id, bool):
                    ids.append(repo_id)
                    remaining.remove(name)
            if len(entries) < 100:
                break
            page += 1
    if remaining:
        raise SystemScopeError("The GitHub App cannot access every repository in this environment.")
    token, expiry = await get_github_app_installation_token_with_expiry(repository_ids=sorted(ids))
    if not token:
        raise SystemScopeError("Could not create a repository-scoped GitHub App token.")
    return token, expiry
