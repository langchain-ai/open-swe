"""Shows Tool Gateway's questions with the connect and approval cards Studio
and Slack already render.

LangChain's MCP adapter answers a server's question (MCP elicitation) with its
own ``mcp_elicitation`` interrupt, which Studio and Slack cannot show yet. For
servers with LangSmith auth, MDA's callback records the question and stops the
call instead, and this middleware shows the card and calls again: the adapter's
loop, with the cards' shapes. Delete this module once Studio and Slack render
``mcp_elicitation``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from managed_deepagents._connection_authorization import (
    PendingCredential,
    ToolCredentialRequiredError,
    credential_authorization_interrupt,
)
from managed_deepagents._credential_gate import connecting_caller
from managed_deepagents._mcp_problem import McpProblemError, cause_of

#: Where Tool Gateway names the connection and authorization session a link is for.
_CREDENTIAL_META = "com.langchain.langsmith/credential"


@dataclass(frozen=True)
class _Question:
    key: str
    message: str
    #: The link to visit to connect, or None for an approval.
    url: str | None
    credential: Mapping[str, Any]


@dataclass
class _Call:
    runtime: object
    #: Whether the run has a person to ask, looked up at the first question.
    person: bool | None = None
    #: Approvals the person gave, answered yes when the call runs again.
    approved: set[str] = field(default_factory=set)
    asked: list[_Question] = field(default_factory=list)


_call: ContextVar[_Call | None] = ContextVar("mda_tool_gateway_call", default=None)


async def answer_tool_gateway_prompt(
    message: str, _response_type: object, params: object, context: object
) -> object:
    """Elicitation callback: answers what the person already approved, declines with
    no one to ask, and otherwise records the question and stops the call."""
    from fastmcp.client.elicitation import ElicitResult  # noqa: PLC0415 - optional dependency
    from mcp_types import ElicitRequestFormParams  # noqa: PLC0415 - optional dependency

    call = _call.get()
    key = str(getattr(context, "request_id", "") or "")
    approval = isinstance(params, ElicitRequestFormParams)
    if call is not None and call.person is None:
        call.person = await asyncio.to_thread(connecting_caller, call.runtime) is not None
    if call is None or not call.person:
        return ElicitResult(action="decline")
    if approval and key in call.approved:
        return ElicitResult(action="accept", content={})
    # The client hands a question's _meta over on the request context.
    meta = getattr(context, "meta", None) or {}
    url = None if approval else str(getattr(params, "url", "") or "")
    call.asked.append(_Question(key, message, url, meta.get(_CREDENTIAL_META) or {}))
    # Answering a link would end the authorization session the connect card shows.
    raise _AskPersonError(key)


class _AskPersonError(RuntimeError):
    """Stops a Tool Gateway call so its question can be shown to the person."""


class _McpPromptsMiddleware(AgentMiddleware[Any, Any, Any]):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = _Call(runtime=request.runtime)
        while True:
            call.asked = []
            token = _call.set(call)
            try:
                result = await handler(request)
            except Exception as error:
                if not call.asked:
                    # Tool Gateway refused the call (over a limit, needs an admin, ...):
                    # the model gets its explanation instead of the run ending.
                    refusal = _refusal(error)
                    if refusal is None:
                        raise
                    return _error(request, str(refusal))
            finally:
                _call.reset(token)
            if not call.asked:
                return result
            refusal = _ask_person(request, call)
            if refusal is not None:
                return refusal


def create_mcp_prompts_middleware() -> AgentMiddleware[Any, Any, Any]:
    """Show Tool Gateway's questions to the run's person."""
    return _McpPromptsMiddleware()


def tool_gateway_prompts(connectors: Sequence[object]) -> list[AgentMiddleware[Any, Any, Any]]:
    """The prompts middleware when any of `connectors` has a server with LangSmith auth."""
    # Imported here: the MCP connector module imports this package.
    from managed_deepagents._connectors.mcp import McpServersDefinition  # noqa: PLC0415
    from managed_deepagents._tool_gateway.auth import uses_langsmith_auth  # noqa: PLC0415

    gateway = any(
        uses_langsmith_auth(server)
        for connector in connectors
        if isinstance(connector, McpServersDefinition)
        for server in connector.config["servers"].values()
    )
    return [create_mcp_prompts_middleware()] if gateway else []


def _ask_person(request: ToolCallRequest, call: _Call) -> ToolMessage | None:
    """Show the card for what the call asked; a tool error when the person says no."""
    links = [question for question in call.asked if question.url is not None]
    if links:
        credentials: dict[str, PendingCredential] = {}
        for question in links:
            slug, auth_id = question.credential.get("slug"), question.credential.get("auth_id")
            url = question.url or ""
            if not (slug and auth_id and urlsplit(url).scheme == "https"):
                return _error(request, "This tool asked for a link this agent cannot show.")
            credentials[str(auth_id)] = {
                "slug": str(slug),
                "kind": "secret" if question.credential.get("kind") == "secret" else "oauth2",
                "connect_url": url,
                "auth_id": str(auth_id),
                "provider": str(question.credential.get("display_name") or slug),
            }
        answer = interrupt(credential_authorization_interrupt(list(credentials.values())))
        connected = answer.get("connected_slugs") if isinstance(answer, Mapping) else None
        if connected is None:
            # An answer to an earlier card: the call runs again and asks again.
            return None
        missing = {
            str(c.get("provider")) for c in credentials.values() if c["slug"] not in connected
        }
        return _error(request, f"{', '.join(missing)} is not connected.") if missing else None
    tool = request.tool_call
    answer = interrupt(
        {
            "action_requests": [
                {
                    "name": tool["name"],
                    "args": dict(tool.get("args") or {}),
                    "description": "\n\n".join(question.message for question in call.asked),
                }
            ],
            "review_configs": [
                {"action_name": tool["name"], "allowed_decisions": ["approve", "reject"]}
            ],
        }
    )
    decisions = answer.get("decisions") if isinstance(answer, Mapping) else None
    if not decisions or not isinstance(decisions[0], Mapping):
        # An answer to an earlier card: the call runs again and asks again.
        return None
    if decisions[0].get("type") != "approve":
        return _error(request, "The user declined this tool call, so it did not run.")
    call.approved.update(question.key for question in call.asked)
    return None


def _refusal(error: BaseException) -> BaseException | None:
    """The Tool Gateway refusal behind a failed call, if that is why it failed."""
    return cause_of(error, McpProblemError) or cause_of(error, ToolCredentialRequiredError)


def _error(request: ToolCallRequest, content: str) -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )


__all__ = [
    "answer_tool_gateway_prompt",
    "create_mcp_prompts_middleware",
]
