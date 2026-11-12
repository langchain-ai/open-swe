"""The incident channel policy and its dashboard settings."""

import logging
import time
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from openswe.config import ENV
from openswe.incidents.models import IncidentPolicy
from openswe.incidents.records import IncidentPolicies
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, required_slack_value, slack_error
from openswe.utils.time import now_iso

logger = logging.getLogger(__name__)
POLICIES = IncidentPolicies()
REQUIRED_SLACK_SCOPES = frozenset(
    {
        "channels:read",
        "channels:join",
        "channels:history",
        "app_mentions:read",
        "chat:write",
        "users:read",
        "users:read.email",
    }
)


def actor_login(actor: dict[str, Any]) -> str | None:
    """The GitHub login of the person behind ``actor``; ``None`` for a system or unlinked actor."""
    login = actor.get("github_login")
    return login if isinstance(login, str) and login else None


async def get_policy() -> IncidentPolicy:
    return await POLICIES.get() or IncidentPolicy()


def channel_allowed(
    channel: dict[str, Any],
    policy: IncidentPolicy,
    *,
    require_prefix: bool = True,
) -> bool:
    public_internal = (
        channel.get("is_channel") is True
        and channel.get("is_private") is False
        and channel.get("is_im") is not True
        and channel.get("is_mpim") is not True
        and channel.get("is_ext_shared") is False
        and channel.get("is_pending_ext_shared") is False
    )
    if not public_internal:
        return False
    return (
        (not require_prefix or str(channel.get("name", "")).startswith(policy.channel_prefix))
        and channel.get("id") not in policy.excluded_channel_ids
        and channel.get("is_archived") is not True
    )


async def _auth_test() -> tuple[dict[str, Any], list[str] | None]:
    async with SlackClient.bot() as client:
        response = await client.auth_test()
    data = response.data if isinstance(response.data, dict) else {}
    header = response.headers.get("x-oauth-scopes") if response.headers else None
    scopes = [scope.strip() for scope in header.split(",")] if isinstance(header, str) else None
    return data, scopes


def _last_operation(policy: IncidentPolicy) -> dict[str, Any]:
    return {"command_id": f"settings:{policy.version}", "status": "applied", "error": None}


async def get_settings() -> dict[str, Any]:
    policy = await get_policy()
    app_id = required_slack_value(ENV.SLACK_APP_ID)
    connection: dict[str, Any] = {
        "workspace_id": policy.workspace_id,
        "slack_app_id": app_id,
        "required_scopes_present": None,
        "verified_at": None,
        "error": None,
    }
    try:
        auth, scopes = await _auth_test()
        team_id = str(auth.get("team_id") or "")
        if team_id:
            connection["workspace_id"] = team_id
        if policy.workspace_id and team_id != policy.workspace_id:
            connection["error"] = "Slack installation belongs to a different workspace."
        else:
            connection["verified_at"] = now_iso()
        if scopes is not None:
            connection["required_scopes_present"] = REQUIRED_SLACK_SCOPES.issubset(scopes)
            if not connection["required_scopes_present"]:
                connection["error"] = (
                    "Slack installation is missing required incident channel scopes."
                )
    except SLACK_REQUEST_ERRORS as exc:
        logger.warning("Slack verification failed", extra={"slack_error": slack_error(exc)})
        connection["error"] = "Slack verification failed; check installation and permissions."
    if policy.slack_app_id and app_id != policy.slack_app_id:
        connection["error"] = (
            connection["error"] or "SLACK_APP_ID differs from the app bound to this policy."
        )
    return {
        "policy": policy.model_dump(),
        "connection": connection,
        "last_operation": _last_operation(policy),
    }


async def update_settings(
    policy: dict[str, Any], expected_version: int, actor: dict[str, Any]
) -> dict[str, str]:
    current = await get_policy()
    if expected_version != current.version:
        raise HTTPException(409, "Settings changed; reload before saving")
    try:
        candidate = IncidentPolicy.model_validate(policy)
    except ValidationError as exc:
        raise HTTPException(
            422,
            "Invalid incident policy: "
            + "; ".join(error["msg"] for error in exc.errors(include_input=False)),
        ) from exc
    # Workspace and app identity are server-owned: the bot token's workspace and
    # SLACK_APP_ID. Client-supplied values are ignored.
    candidate.workspace_id = current.workspace_id
    candidate.slack_app_id = current.slack_app_id
    if candidate.model:
        from openswe.dashboard.options import SUPPORTED_MODEL_IDS

        if candidate.model not in SUPPORTED_MODEL_IDS:
            raise HTTPException(422, "Unsupported incident model")
    if candidate.enabled:
        app_id = required_slack_value(ENV.SLACK_APP_ID)
        try:
            auth, _ = await _auth_test()
        except Exception as exc:
            raise HTTPException(
                422, "Slack verification failed; check installation and permissions"
            ) from exc
        workspace_id = str(auth.get("team_id") or "")
        if not workspace_id:
            raise HTTPException(422, "Slack installation did not report a workspace")
        candidate.workspace_id, candidate.slack_app_id = workspace_id, app_id
    candidate.version = current.version + 1
    candidate.enabled_at = (
        time.time() if candidate.enabled and not current.enabled else current.enabled_at
    )
    await POLICIES.put(candidate, by=actor_login(actor))
    logger.info(
        "Incident settings updated", extra={"actor": actor.get("id"), "version": candidate.version}
    )
    return {"command_id": f"settings:{candidate.version}", "status": "applied"}
