"""Tool error handling middleware.

Wraps all tool calls in try/except so that unhandled exceptions are
returned as error ToolMessages instead of crashing the agent run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
)
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import SandboxClientError

logger = logging.getLogger(__name__)

SANDBOX_RECREATED_AFTER_CLIENT_ERROR = "sandbox_recreated_after_client_error"


def _get_name(candidate: object) -> str | None:
    if not candidate:
        return None
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        name = candidate.get("name")
    else:
        name = getattr(candidate, "name", None)
    return name if isinstance(name, str) and name else None


def _extract_tool_name(request: ToolCallRequest | None) -> str | None:
    if request is None:
        return None
    for attr in ("tool_call", "tool_name", "name"):
        name = _get_name(getattr(request, attr, None))
        if name:
            return name
    return None


def _to_error_payload(e: Exception, request: ToolCallRequest | None = None) -> dict[str, str]:
    data: dict[str, str] = {
        "error": str(e),
        "error_type": e.__class__.__name__,
        "status": "error",
    }
    tool_name = _extract_tool_name(request)
    if tool_name:
        data["name"] = tool_name
    return data


def _to_sandbox_recreated_payload(
    e: SandboxClientError,
    sandbox_id: str,
    request: ToolCallRequest | None = None,
) -> dict[str, str]:
    data: dict[str, str] = {
        "status": "error",
        "error_type": e.__class__.__name__,
        "previous_error": str(e),
        "recovery": SANDBOX_RECREATED_AFTER_CLIENT_ERROR,
        "sandbox_id": sandbox_id,
        "error": (
            "The previous sandbox became unreachable mid-run. A fresh sandbox "
            f"({sandbox_id}) has been created and cached for this thread. "
            "Retry the last tool call; if repository files are missing, re-clone or "
            "reinitialize the workspace first."
        ),
    }
    tool_name = _extract_tool_name(request)
    if tool_name:
        data["name"] = tool_name
    return data


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    if isinstance(request.tool_call, dict):
        return request.tool_call.get("id")
    return None


def _get_configurable(request: ToolCallRequest) -> Mapping[str, Any]:
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    config: Mapping[str, Any] | None = (
        runtime_config if isinstance(runtime_config, Mapping) else None
    )
    if config is None:
        try:
            maybe_config = get_config()
        except Exception:
            logger.exception("Failed to read runnable config while handling sandbox error")
            return {}
        config = maybe_config if isinstance(maybe_config, Mapping) else None
    if config is None:
        return {}

    configurable = config.get("configurable", {})
    return configurable if isinstance(configurable, Mapping) else {}


def _get_thread_id(configurable: Mapping[str, Any]) -> str | None:
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


async def _recreate_sandbox_for_thread(thread_id: str, configurable: Mapping[str, Any]) -> str:
    from agent.server import _configure_git_identity, _recreate_sandbox, client
    from agent.utils.auth import resolve_github_token
    from agent.utils.sandbox_state import set_sandbox_backend

    repo = configurable.get("repo")
    repo_config = repo if isinstance(repo, dict) else None
    proxy_repositories = (
        [repo_config["name"]] if repo_config and isinstance(repo_config.get("name"), str) else None
    )
    proxy_token = None
    if configurable.get("source") == "dashboard" and repo_config is None:
        proxy_token, _expires_at = await resolve_github_token(
            {"configurable": dict(configurable)}, thread_id
        )
    sandbox_backend = await _recreate_sandbox(
        thread_id,
        github_proxy_token=proxy_token,
        github_proxy_repositories=proxy_repositories,
        repo=repo_config,
    )
    sandbox_backend = set_sandbox_backend(thread_id, sandbox_backend)
    await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": sandbox_backend.id})
    await _configure_git_identity(sandbox_backend)
    return sandbox_backend.id


def _sandbox_recreated_tool_message(
    e: SandboxClientError,
    sandbox_id: str,
    request: ToolCallRequest,
) -> ToolMessage:
    data = _to_sandbox_recreated_payload(e, sandbox_id, request)
    return ToolMessage(
        content=json.dumps(data),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


def _generic_error_tool_message(e: Exception, request: ToolCallRequest) -> ToolMessage:
    data = _to_error_payload(e, request)
    return ToolMessage(
        content=json.dumps(data),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


class ToolErrorMiddleware(AgentMiddleware):
    """Normalize tool execution errors into predictable payloads.

    Catches any exception thrown during a tool call and converts it into
    a ToolMessage with status="error" so the LLM can see the failure and
    self-correct, rather than crashing the entire agent run.
    """

    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except SandboxClientError as e:
            logger.exception("Sandbox error during tool call handling; request=%r", request)
            configurable = _get_configurable(request)
            thread_id = _get_thread_id(configurable)
            if thread_id:
                try:
                    sandbox_id = await _recreate_sandbox_for_thread(thread_id, configurable)
                    return _sandbox_recreated_tool_message(e, sandbox_id, request)
                except Exception:
                    logger.exception("Failed to recreate sandbox for thread %s", thread_id)
            return _generic_error_tool_message(e, request)
        except Exception as e:
            logger.exception("Error during tool call handling; request=%r", request)
            return _generic_error_tool_message(e, request)
