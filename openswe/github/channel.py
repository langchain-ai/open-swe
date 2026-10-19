"""Routes for MDA's GitHub channel: @openswe mentions on issues.

Open SWE's App keeps its webhook at ``/webhooks/github``, which forwards issue
deliveries to the MDA deployment's GitHub channel (``openswe.github.forwarding``).
The channel verifies GitHub's signature, owns the conversation's thread and
starts its runs; its ``on_event`` names this endpoint, which it asks what each
event starts. Each run carries the backend's run-complete webhook. The backend decides
everything else, as for its own runs: who may trigger, the prompt, the workspace,
and the signed run token the run calls back with.
"""

import logging
import re
from typing import Any, Final, Literal, cast, get_args
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from managed_deepagents import EndpointAnswer, EndpointRequest
from pydantic import JsonValue

from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.github import webhook as service
from openswe.mda_channels.auth import require_mda_channel
from openswe.threads.router import adopt_channel_run
from openswe.threads.runtimes import RUNTIMES
from openswe.webhooks import common
from openswe.workspaces.routing import repo_is_routable

logger = logging.getLogger(__name__)

CHANNEL: Final = "github"
# What the channel starts with while the thread waits: nothing, it only notes Open SWE acts.
_ACTS_WHILE_INTERRUPTED: Final = "Open SWE was mentioned while the thread waits."
# GitHub caps an issue or comment body at 65,536 characters; a payload near this is not one.
_MAX_BODY_BYTES: Final = 2_000_000
# The run metadata keys MDA accepts from a handler.
_METADATA_KEY: Final = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_RESERVED_METADATA_KEY: Final = re.compile(r"(mda_|langgraph_).*", re.IGNORECASE)

IssueEventKey = Literal[
    "issues.opened",
    "issues.edited",
    "issues.reopened",
    "issue_comment.created",
    "issue_comment.edited",
]
_ISSUE_EVENT_KEYS: frozenset[str] = frozenset(get_args(IssueEventKey))


def _limit_size(request: Request) -> None:
    """Refuse a body that is no GitHub event before reading it."""
    length = request.headers.get("content-length", "")
    if not length.isdigit() or int(length) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="The event is too large")


router = APIRouter(
    prefix="/github/channel",
    dependencies=[Depends(require_mda_channel), Depends(_limit_size)],
)


async def trigger_rejection(payload: dict[str, Any], event_type: str) -> str | None:
    """Why an issue event starts no run, as ``/webhooks/github`` decides it; None when it does."""
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        return "The event names no issue"
    if issue.get("pull_request"):
        return "Pull request comments stay on Open SWE's own intake"
    repository = payload.get("repository") or {}
    repo_config = {
        "owner": str((repository.get("owner") or {}).get("login") or ""),
        "name": str(repository.get("name") or ""),
    }
    if not common.is_repo_allowed(repo_config):
        return "Repository not in allowlist"
    if not await repo_is_routable(repo_config["owner"], repo_config["name"]):
        return "Repository is not assigned to a workspace"
    if event_type == "issues":
        if payload.get("action") not in common.SUPPORTED_GH_ISSUE_ACTIONS:
            return "Unsupported GitHub issue action"
        changes = payload.get("changes") or {}
        if payload.get("action") == "edited" and not any(f in changes for f in ("body", "title")):
            return "Issue edit did not change title or body"
        text = f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}"
    else:
        if payload.get("action") not in common.SUPPORTED_GH_COMMENT_ACTIONS["issue_comment"]:
            return "Unsupported GitHub issue_comment action"
        sender_login = str((payload.get("sender") or {}).get("login") or "")
        if not await service.is_accepted_commenter(sender_login):
            return "Sender is not a registered Open SWE user"
        text = str((payload.get("comment") or {}).get("body") or "")
    if not common.mentions_open_swe(text):
        return f"Does not mention {common.describe_open_swe_tags()}"
    gate_rejection = await common.enforce_public_repo_org_gate(payload, event_type)
    if gate_rejection is not None:
        return gate_rejection["reason"]
    return None


def _run_metadata(metadata: dict[str, Any]) -> dict[str, JsonValue]:
    """The run's metadata, without keys MDA refuses from a handler."""
    return {
        key: value
        for key, value in metadata.items()
        if _METADATA_KEY.fullmatch(key) and not _RESERVED_METADATA_KEY.fullmatch(key)
    }


async def _prepare_run(
    payload: dict[str, Any], delivery_id: str, thread_id: str, event_type: str
) -> EndpointAnswer | None:
    prepared = await service.prepare_github_issue_run(payload, event_type, thread_id)
    if prepared is None:
        return None
    await adopt_channel_run(thread_id, CHANNEL)
    run_config = await prepare_durable_run(
        "agent",
        {"configurable": prepared.configurable},
        {
            **common.AGENT_VERSION_METADATA,
            CHANNEL_METADATA_KEY: CHANNEL,
            "github_delivery": delivery_id,
        },
        source="github_issue",
    )
    rewritten = await RUNTIMES.mda_runtime.run_payload(
        thread_id, {"assistant_id": "agent", "config": cast(JsonValue, run_config)}
    )
    run: EndpointAnswer = {
        "input": cast(JsonValue, prepared.input),
        "context": cast(dict[str, JsonValue], rewritten["context"]),
        "metadata": _run_metadata(run_config["metadata"]),
    }
    if COMPLETION_WEBHOOK_URL:
        run["webhook"] = COMPLETION_WEBHOOK_URL
    return run


@router.post("/events")
async def github_channel_event(
    call: EndpointRequest[dict[str, Any]],
) -> EndpointAnswer | None:
    """The run an @openswe mention on an issue starts on the channel's thread, or ``None``.

    While the thread waits on an interrupt nothing starts: a non-null answer only
    tells the channel Open SWE would act, so it points the issue to the decision.
    """
    event, thread = call["event"], call["thread"]
    key, payload, delivery_id = event["key"], event["payload"], event["deliveryId"]
    if key not in _ISSUE_EVENT_KEYS or not delivery_id:
        raise HTTPException(status_code=422, detail="Unsupported GitHub event")
    try:
        thread_id = str(UUID(thread["id"]))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid thread") from error
    event_type, _, action = key.partition(".")
    if payload.get("action") != action:
        raise HTTPException(status_code=422, detail="The payload's action is not the event's")
    reason = await trigger_rejection(payload, event_type)
    if reason is not None:
        logger.info(
            "MDA's GitHub channel event starts no run",
            extra={"github_event": key, "github_delivery": delivery_id},
        )
        return None
    if thread.get("interrupted"):
        # Nothing starts while the thread waits; no reaction or metadata either.
        return {"input": _ACTS_WHILE_INTERRUPTED}
    return await _prepare_run(payload, delivery_id, thread_id, event_type)
