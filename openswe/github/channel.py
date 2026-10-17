"""Routes for MDA's GitHub channel: @openswe mentions on issues.

Open SWE's App keeps its webhook at ``/webhooks/github``, which forwards issue
deliveries to the MDA deployment's GitHub channel (``openswe.github.forwarding``).
The channel verifies GitHub's signature, owns the conversation's thread and
starts its runs; its handler asks this route what to start. The backend decides
everything else, as for its own runs: who may trigger, the prompt, the workspace,
and the signed run token the run calls back with.
"""

import logging
import re
from typing import Any, Final, Literal, TypedDict, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openswe.dispatch import CHANNEL_METADATA_KEY, prepare_durable_run
from openswe.github import webhook as service
from openswe.input_messages import RunInput
from openswe.mda_channels.auth import require_mda_channel
from openswe.threads.router import adopt_channel_run
from openswe.threads.runtimes import RUNTIMES
from openswe.webhooks import common
from openswe.workspaces.routing import repo_is_routable

logger = logging.getLogger(__name__)

CHANNEL: Final = "github"
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


def _limit_size(request: Request) -> None:
    """Refuse a body that is no GitHub event before reading it."""
    length = request.headers.get("content-length", "")
    if not length.isdigit() or int(length) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="The event is too large")


router = APIRouter(
    prefix="/github/channel",
    dependencies=[Depends(require_mda_channel), Depends(_limit_size)],
)


class ChannelEvent(BaseModel):
    """An event MDA's GitHub channel verified, as its handler hands it over."""

    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    """The conversation's thread: the run starts on it, or it waits on an interrupt."""
    interrupted: bool = False
    """The thread waits on an interrupt, so nothing starts: only whether Open SWE acts."""
    delivery_id: str = Field(min_length=1, max_length=200)
    event: IssueEventKey
    payload: dict[str, Any]


class PreparedRun(TypedDict):
    """What the channel starts the run with."""

    input: RunInput
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]


class PreparedGitHubRun(TypedDict):
    """The run the channel starts, if any, and whether Open SWE acts on the event at all."""

    run: PreparedRun | None
    acts: bool


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


async def _prepare_run(event: ChannelEvent, event_type: str) -> PreparedRun | None:
    thread_id = str(event.thread_id)
    prepared = await service.prepare_github_issue_run(event.payload, event_type, thread_id)
    if prepared is None:
        return None
    await adopt_channel_run(thread_id, CHANNEL)
    # Signed into the run token too, so the run's after_agent hook completes it.
    configurable = {**prepared.configurable, CHANNEL_METADATA_KEY: CHANNEL}
    run_config = await prepare_durable_run(
        "agent",
        {"configurable": configurable},
        {
            **common.AGENT_VERSION_METADATA,
            CHANNEL_METADATA_KEY: CHANNEL,
            "github_delivery": event.delivery_id,
        },
        source="github_issue",
    )
    rewritten = await RUNTIMES.mda_runtime.run_payload(
        thread_id, {"assistant_id": "agent", "config": cast(JsonValue, run_config)}
    )
    return {
        "input": prepared.input,
        "context": cast(dict[str, JsonValue], rewritten["context"]),
        "metadata": _run_metadata(run_config["metadata"]),
    }


@router.post("/runs")
async def prepare_github_run(event: ChannelEvent) -> PreparedGitHubRun:
    """Prepare the run an @openswe mention on an issue starts on the channel's thread."""
    event_type, _, action = event.event.partition(".")
    if event.payload.get("action") != action:
        raise HTTPException(status_code=422, detail="The payload's action is not the event's")
    reason = await trigger_rejection(event.payload, event_type)
    if reason is not None:
        logger.info(
            "MDA's GitHub channel event starts no run",
            extra={"github_event": event.event, "github_delivery": event.delivery_id},
        )
        return {"run": None, "acts": False}
    if event.interrupted:
        # Nothing starts while the thread waits; no reaction or metadata either.
        return {"run": None, "acts": True}
    return {"run": await _prepare_run(event, event_type), "acts": True}
