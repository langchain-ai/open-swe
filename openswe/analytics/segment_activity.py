"""Minimized runtime activity, separate from human UI intent and lifecycle outcomes."""

import logging
import re
from datetime import UTC, datetime
from uuid import UUID, uuid5

from langchain_core.messages import messages_from_dict
from pydantic import JsonValue
from runtime_api.loop import RunStarted, ToolResultReport
from runtime_api.provenance import Provenance

from openswe.agent_api.tokens import RemoteRun
from openswe.analytics.events import EVENT_NAMESPACE
from openswe.analytics.identity import opaque_id
from openswe.analytics.segment import SegmentDelivery
from openswe.config import ENV
from openswe.database.analytics import workspace_id
from openswe.users.models import User

logger = logging.getLogger(__name__)

_BUILTINS = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "execute",
        "eval",
        "search_tools",
        "web_search",
        "fetch_url",
        "http_request",
        "browser_run",
        "browser_snapshot",
        "browser_screenshot",
        "browser_record",
        "background_execute",
        "monitor",
        "task",
        "start_async_task",
        "control_worker",
        "message_task_thread",
        "start_thread",
        "get_thread",
        "list_threads",
        "manage_thread",
        "open_pull_request",
        "link_pull_request",
        "request_pr_review",
        "request_human_review",
        "get_human_review_status",
        "remember",
        "forget",
        "publish_artifact",
        "output_iframe",
        "preview_app",
        "publish_app",
        "create_sandbox_file_download_url",
        "slack_reply",
        "slack_no_reply_needed",
        "goal_create",
        "goal_set_objective",
        "goal_get",
        "goal_set_state",
        "goal_complete",
        "goal_pause",
        "todowrite",
        "write_todos",
        "read_todos",
        "schedule_thread_wakeup",
        "connect_managed_tools",
        "switch_to_performance_model",
        "external",
        "unknown",
    }
)


class SegmentActivity:
    def __init__(self, run: RemoteRun) -> None:
        self.run = run

    @staticmethod
    def safe_tool_name(value: str) -> str:
        name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
        if name in _BUILTINS:
            return name
        return "external" if name.startswith(("mcp_", "plugin_")) else "unknown"

    @staticmethod
    def source(value: object) -> str:
        return (
            value
            if isinstance(value, str)
            and value in {"web", "dashboard", "slack", "github", "linear", "api", "scheduled"}
            else "unknown"
        )

    @staticmethod
    async def canonical(key: object) -> UUID | None:
        if not isinstance(key, str):
            return None
        try:
            user_id = UUID(key.removeprefix("user:"))
        except ValueError:
            return None
        user = await User.get(user_id)
        return user.id if user else None

    @classmethod
    async def actor(cls, provenance: Provenance) -> UUID | None:
        if provenance.sender.startswith("user:"):
            return await cls.canonical(provenance.sender)
        provider, _, external_id = provenance.sender.partition(":")
        if provider == "github" and external_id.isdecimal():
            user = await User.for_identity("github", external_id)
        elif provider == "slack" and re.fullmatch(r"[UW][A-Z0-9]+", external_id):
            user = await User.for_identity("slack", external_id)
        else:
            return None
        return user.id if user else None

    def properties(self) -> dict[str, JsonValue]:
        return {
            "thread_id": str(opaque_id("thread", self.run.thread_id)),
            "run_kind": {
                "agent": "agent",
                "reviewer": "reviewer",
                "review-scout": "review_scout",
            }.get(self.run.assistant_id, "unknown"),
            "environment": ENV.DD_ENV.get(),
        }

    async def request_started(self, request: RunStarted, *, carried: bool) -> None:
        if not ENV.SEGMENT_BROWSER_WRITE_KEY.get().strip():
            return
        try:
            origin = None
            for message in reversed(messages_from_dict(request.humans)):
                provenance = Provenance.of(message)
                if provenance is None or provenance.kind != "context":
                    origin = provenance
                    break
            trigger = origin.kind if origin else "unknown"
            current = not carried and (
                bool(request.invocation_id)
                and request.invocation_id == self.run.configurable.get("invocation_id")
                or bool(request.run_id)
                and request.run_id == self.run.configurable.get("run_id")
            )
            actor = await self.actor(origin) if origin and origin.human else None
            owner = (
                await self.canonical(self.run.configurable.get("user_id"))
                if actor is None and current
                else None
            )
            invocation = request.invocation_id or (
                self.run.configurable.get("invocation_id") if current else None
            )
            invocation = invocation if isinstance(invocation, str) else request.run_id
            source = self.source(origin.surface if origin else None)
            if source == "unknown":
                source = self.source(
                    request.sender.source
                    if request.sender
                    else (self.run.settings.source if current else None)
                )
            occurred_at = datetime.now(UTC)
            started = self.run.configurable.get("invocation_started_at") if current else None
            if isinstance(started, str):
                parsed = datetime.fromisoformat(started)
                if parsed.tzinfo is not None:
                    occurred_at = parsed
            properties = self.properties()
            properties.update(
                activity_kind={"human": "human_request", "system": "automatic_work"}.get(
                    trigger, "unknown"
                ),
                trigger_kind=trigger,
                source=source,
                attribution_role="actor" if actor else "owner" if owner else "unknown",
                run_id=str(opaque_id("run", invocation)),
                runtime_run_id=str(opaque_id("runtime_run", request.run_id)),
            )
            workspace = workspace_id()
            await SegmentDelivery.enqueue_track(
                message_id=uuid5(
                    EVENT_NAMESPACE, f"{workspace}:request:{self.run.thread_id}:{invocation}"
                ),
                occurred_at=occurred_at,
                workspace_id=workspace,
                event="agents_request_started",
                user_id=actor or owner,
                anonymous_id=opaque_id("thread", self.run.thread_id),
                properties=properties,
            )
        except Exception:
            logger.warning("Segment request activity capture failed")

    async def tool_executed(self, request: ToolResultReport, *, is_error: bool) -> None:
        if not ENV.SEGMENT_BROWSER_WRITE_KEY.get().strip():
            return
        try:
            own_run = bool(request.run_id) and request.run_id == self.run.configurable.get("run_id")
            owner = await self.canonical(self.run.configurable.get("user_id")) if own_run else None
            properties = self.properties()
            properties.update(
                activity_kind="agent_work",
                attribution_role="owner" if owner else "unknown",
                source=self.source(self.run.settings.source) if own_run else "unknown",
                runtime_run_id=str(opaque_id("runtime_run", request.run_id)),
                tool=self.safe_tool_name(request.name),
                is_error=is_error,
            )
            workspace = workspace_id()
            await SegmentDelivery.enqueue_track(
                message_id=uuid5(
                    EVENT_NAMESPACE,
                    f"{workspace}:tool:{self.run.thread_id}:{request.run_id}:"
                    f"{request.namespace!r}:{request.tool_call_id}",
                ),
                occurred_at=datetime.now(UTC),
                workspace_id=workspace,
                event="agents_tool_executed",
                user_id=owner,
                anonymous_id=opaque_id("thread", self.run.thread_id),
                properties=properties,
            )
        except Exception:
            logger.warning("Segment tool activity capture failed")
