"""Saved incident passes and capabilities for the main agent graph."""

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import langgraph_sdk
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from agent.incidents import service, slack
from agent.incidents.engine import INCIDENT_PROMPT, ReportDraft, finalize_report, message_context
from agent.incidents.evidence_tools import EvidenceCollector
from agent.incidents.models import Evidence, IncidentMessage, IncidentPolicy, IncidentReport
from agent.middleware.conversation_offloading import ConversationOffloadingMiddleware
from agent.middleware.trace import OpenSWEMiddleware
from agent.store import TypedStore
from agent.tool_loaders.workspace_mcp import load_workspace_mcp_tools


class IncidentPass(BaseModel):
    id: str
    incident_id: str
    thread_id: str
    provider_scope: str = ""
    evidence_scope: str = ""
    policy: IncidentPolicy
    messages: list[IncidentMessage] = Field(default_factory=list)
    question: str | None = None
    explicit: bool = False
    started_at: float = Field(default_factory=time.time)
    run_id: str | None = None
    dispatch_started: bool = False
    cancelled: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    related_incident_ids: list[str] = Field(default_factory=list)
    report: IncidentReport | None = None


PASSES = TypedStore(["incidents", "passes"], IncidentPass)
_READ_TOOLS = frozenset(
    {
        "search_datadog_logs",
        "search_datadog_spans",
        "search_datadog_metrics",
        "search_datadog_dashboards",
        "get_datadog_dashboard",
        "get_datadog_trace",
        "search_datadog_monitors",
        "get_datadog_monitor",
        "search_datadog_events",
        "search_datadog_hosts",
        "search_datadog_entities",
    }
)


async def evidence_scope() -> str:
    from agent.dashboard.workspace_mcps import list_workspace_mcp_records

    connections = [
        (record.name, record.revision, record.updated_at)
        for record in await list_workspace_mcp_records()
        if record.enabled and _READ_TOOLS.intersection(record.allowed_tools)
    ]
    return service.fingerprint(sorted(connections)) if connections else ""


class IncidentSession:
    def __init__(self, saved: IncidentPass) -> None:
        self.saved = saved
        end = datetime.fromtimestamp(saved.started_at, UTC)
        self.collector = EvidenceCollector(
            saved.policy,
            window_start=end - timedelta(hours=2),
            window_end=end,
            before_tool_call=self.check,
        )
        message_context(saved.messages, saved.policy, self.collector)
        existing = {e.id for e in self.collector.evidence}
        self.collector.evidence.extend(e for e in saved.evidence if e.id not in existing)
        self.collector.checked.extend(saved.checked)
        self.collector.gaps.extend(saved.gaps)
        self.prompt = INCIDENT_PROMPT.format(schema=json.dumps(ReportDraft.model_json_schema())) + (
            "\nThis is a persistent incident conversation. Earlier turns are historical context. "
            "Recheck old observations; only cite evidence IDs in this pass or its tools. "
            "Maintain the investigation across turns and answer the latest directed question. "
            "Incident lifecycle is separate from whether the agent is watching."
        )
        self.tools: list[BaseTool] = self.collector.tools()

    async def check(self) -> None:
        from agent.incidents.worker import channel_receipts, command_for_receipt, stale_control

        saved = await PASSES.get(self.saved.id)
        record = await service.INVESTIGATIONS.get(self.saved.incident_id)
        policy = await service.get_policy()
        if (
            saved is None
            or saved.cancelled
            or record is None
            or record.expired
            or record.active_pass_id != self.saved.id
            or record.agent_thread_id != self.saved.thread_id
            or record.workspace_id != policy.workspace_id
            or record.channel_id in policy.excluded_channel_ids
            or record.reason == "code_channel"
            or not policy.enabled
            or policy.version != self.saved.policy.version
        ):
            raise PermissionError("Incident pass was revoked")
        if time.time() - self.saved.started_at >= policy.max_pass_seconds:
            raise TimeoutError("Incident pass reached its time budget")
        for receipt in await channel_receipts(record):
            action, _ = await command_for_receipt(receipt, policy)
            if action in {"pause", "complete"} and not stale_control(record, receipt, action):
                raise PermissionError("Incident stop requested")
        info = await slack.channel_info(record.channel_id)
        if (
            info.get("id") != record.channel_id
            or info.get("is_member") is not True
            or not slack.channel_allowed(info, policy, for_read=self.saved.explicit)
        ):
            raise PermissionError("Incident channel access revoked")
        if not self.saved.explicit and record.status in {"paused", "completed"}:
            raise PermissionError("Incident is no longer watching")
        from agent.incidents.providers import analysis_scope

        if await analysis_scope(record) != self.saved.provider_scope:
            raise PermissionError("Incident provider access changed")
        if await evidence_scope() != self.saved.evidence_scope:
            raise PermissionError("Incident evidence access changed")
        from agent.incidents.documents import require_access

        for incident_id in self.saved.related_incident_ids:
            await require_access(incident_id)

    async def persist_evidence(self) -> None:
        saved = await PASSES.get(self.saved.id)
        if saved is None or saved.cancelled:
            raise PermissionError("Incident pass was revoked")
        saved.evidence = self.collector.evidence
        saved.checked = self.collector.checked
        saved.gaps = self.collector.gaps
        saved.related_incident_ids = self.saved.related_incident_ids
        await PASSES.put(saved.id, saved)

    async def load_tools(self) -> None:
        for remote in await load_workspace_mcp_tools():
            original = (remote.metadata or {}).get("mcp_tool_name")
            if original not in _READ_TOOLS:
                continue
            self.tools.append(self._evidence_tool(remote))

        async def search_incidents(query: str = "") -> dict[str, Any]:
            """Find readable past incidents and their curated postmortems."""
            from agent.incidents.documents import search_history

            await self.check()
            result = await search_history(q=query, limit=20)
            self.saved.related_incident_ids = sorted(
                set(self.saved.related_incident_ids) | {item["id"] for item in result["items"]}
            )
            await self.persist_evidence()
            return result

        async def read_incident(incident_id: str) -> dict[str, Any]:
            """Read the postmortem of another accessible incident as historical context."""
            from agent.incidents.documents import document_context

            await self.check()
            context = await document_context(incident_id)
            self.saved.related_incident_ids = sorted(
                set(self.saved.related_incident_ids) | {incident_id}
            )
            await self.persist_evidence()
            return self.collector.record_observation(
                source="incident",
                url="",
                summary="Historical incident context; not proof of the current cause.",
                query=incident_id,
                content=context,
            )

        self.tools.extend(
            [
                StructuredTool.from_function(coroutine=search_incidents),
                StructuredTool.from_function(coroutine=read_incident),
            ]
        )

    def _evidence_tool(self, remote: BaseTool) -> BaseTool:
        async def invoke(**kwargs: Any) -> dict[str, Any]:
            async def read() -> dict[str, Any]:
                result = await remote.ainvoke(
                    {
                        "type": "tool_call",
                        "id": "incident-evidence",
                        "name": remote.name,
                        "args": kwargs,
                    }
                )
                artifact = result.artifact if isinstance(result, ToolMessage) else None
                if (isinstance(result, ToolMessage) and result.status == "error") or (
                    isinstance(artifact, dict) and artifact.get("isError") is True
                ):
                    raise ValueError("Workspace MCP evidence operation failed")
                content = result.content if isinstance(result, ToolMessage) else result
                if isinstance(artifact, dict) and artifact.get("structured_content") is not None:
                    content = artifact["structured_content"]
                if isinstance(content, dict) and content.get("isError") is True:
                    raise ValueError("Workspace MCP evidence operation failed")
                return self.collector.record_observation(
                    source="mcp",
                    url="",
                    summary=f"Observation from {(remote.metadata or {}).get('mcp_tool_name', remote.name)}.",
                    query=json.dumps(kwargs),
                    content=content,
                )

            return await self.collector.run_evidence("Workspace MCP evidence", read)

        return StructuredTool.from_function(
            coroutine=invoke,
            name=remote.name,
            description=remote.description,
            args_schema=remote.args_schema,
        )


async def load_incident_session(config: Mapping[str, Any]) -> IncidentSession | None:
    cfg = config.get("configurable") or {}
    thread_id = cfg.get("thread_id")
    if not thread_id:
        return None
    thread = await langgraph_sdk.get_client().threads.get(thread_id)
    metadata = thread.get("metadata") or {}
    if metadata.get("source") in {"incidents", "incidents_coordinator"}:
        raise PermissionError("Operational incident threads cannot run a conversational agent")
    if metadata.get("source") != "incidents_agent":
        if cfg.get("source") == "incidents_agent" or cfg.get("incident_pass_id"):
            raise PermissionError("Incident binding is missing")
        return None
    if metadata.get("owner_type") != "system" or metadata.get("visibility") != "public":
        raise PermissionError("Incident conversation must be system owned")
    if (
        cfg.get("github_login")
        or cfg.get("user_email")
        or cfg.get("local_run")
        or cfg.get("source") == "desktop"
    ):
        raise PermissionError("Incident passes cannot carry personal execution identity")
    saved = await PASSES.get(str(cfg.get("incident_pass_id") or ""))
    if (
        not saved
        or saved.thread_id != thread_id
        or saved.incident_id != metadata.get("incident_id")
    ):
        raise PermissionError("Incident pass does not match its saved binding")
    session = IncidentSession(saved)
    await session.check()
    await session.load_tools()
    return session


class IncidentMiddleware(OpenSWEMiddleware):
    """Restrict model and tool execution, and finalize evidence-backed reports."""

    def __init__(self, session: IncidentSession) -> None:
        self.session = session
        self.allowed = {tool.name for tool in session.tools} | {
            "read_file",
            "ls",
            "glob",
            "grep",
            "write_todos",
        }
        self.error: Exception | None = None

    async def _check(self) -> None:
        if self.error is not None:
            raise self.error
        try:
            await self.session.check()
        except Exception as exc:
            self.error = exc
            raise

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        await self._check()
        return await handler(
            request.override(
                system_message=SystemMessage(content=self.session.prompt),
                tools=[
                    tool for tool in request.tools if getattr(tool, "name", None) in self.allowed
                ],
            )
        )

    async def awrap_tool_call(self, request: Any, handler: Callable[..., Awaitable[Any]]) -> Any:
        await self._check()
        if request.tool_call["name"] not in self.allowed:
            raise PermissionError("Tool is not available to incident conversations")
        result = await handler(request)
        await self._check()
        await self.session.persist_evidence()
        return result

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:
        await self._check()
        messages = state.get("messages", [])
        last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last is None or last.tool_calls:
            raise ValueError("No valid evidence report was finalized")
        text = last.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        draft = ReportDraft.model_validate_json(text)
        saved = await PASSES.get(self.session.saved.id)
        if saved is None or saved.cancelled:
            raise PermissionError("Incident pass was revoked")
        saved.report = finalize_report(draft, self.session.collector)
        saved.report.id = saved.id
        await PASSES.put(saved.id, saved)


class IncidentOffloadingMiddleware(ConversationOffloadingMiddleware):
    """Apply incident access checks to the compaction model as well."""

    def __init__(self, model: Any, backend: Any, session: IncidentSession) -> None:
        super().__init__(model, backend)
        self.session = session

    async def _acreate_summary(self, messages_to_summarize: list[Any]) -> str:
        await self.session.check()
        summary = await super()._acreate_summary(messages_to_summarize)
        await self.session.check()
        return summary
