import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Literal, Never, cast

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware
from agent.run_config import RunConfig
from agent.source_context import SourceContext
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_FINAL_TOOLS = frozenset({"slack_thread_reply", "no_slack_response_needed"})
_MAX_REPAIR_ATTEMPTS = 2
_REPAIR_INVOCATION_KEY = "open_swe_slack_disposition_repair"
_REPAIR_ATTEMPT_KEY = "open_swe_slack_disposition_repair_attempt"
_REPAIR_PROMPT = (
    "This Slack invocation has no successful final response disposition. Call exactly one of the "
    "available tools now: slack_thread_reply with response_type='final' to deliver the final "
    "answer or outcome, or no_slack_response_needed with a non-empty reason when silence is "
    "intentional. A progress reply does not satisfy this requirement."
)
_SURFACE_CHANGED_ERROR = (
    "The conversation moved away from Slack before this call executed. Continue on the active "
    "surface instead of posting to Slack."
)
_SURFACE_UNKNOWN_ERROR = (
    "The active response surface could not be verified before this call executed. Do not post to "
    "Slack until the active surface can be confirmed."
)

type ToolCommand = Command[Literal["tools", "model", "end"]]


class MissingSlackResponseDispositionError(RuntimeError):
    pass


def _tool_name(tool: object) -> str | None:
    if isinstance(tool, Mapping):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def _has_tool_calls(response: ModelResponse[object]) -> bool:
    return any(isinstance(message, AIMessage) and message.tool_calls for message in response.result)


def _repair_attempts(messages: Sequence[BaseMessage], invocation_id: str) -> int:
    explicit: list[int] = []
    legacy = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        if message.response_metadata.get(_REPAIR_INVOCATION_KEY) != invocation_id:
            continue
        attempt = message.response_metadata.get(_REPAIR_ATTEMPT_KEY)
        if isinstance(attempt, int) and attempt > 0:
            explicit.append(attempt)
        else:
            legacy += 1
    return max([legacy, *explicit], default=0)


def _tag_repair(response: ModelResponse[object], invocation_id: str, attempt: int) -> None:
    for message in response.result:
        if isinstance(message, AIMessage):
            message.response_metadata = {
                **message.response_metadata,
                _REPAIR_INVOCATION_KEY: invocation_id,
                _REPAIR_ATTEMPT_KEY: attempt,
            }


def _payload(message: ToolMessage) -> Mapping[str, object] | None:
    if isinstance(message.artifact, Mapping):
        return cast(Mapping[str, object], message.artifact)
    if not isinstance(message.content, str):
        return None
    try:
        value = json.loads(message.content)
    except json.JSONDecodeError, TypeError:
        return None
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _invocation_id() -> str | None:
    try:
        config = get_config()
    except RuntimeError:
        return None
    cfg = RunConfig.from_config(config)
    for value in (cfg.invocation_id, config.get("run_id"), cfg.run_id):
        if value:
            return str(value)
    return None


def _configured_slack_run(cfg: RunConfig) -> bool:
    return bool(
        cfg.source == "slack"
        and cfg.slack_thread
        and cfg.slack_thread.channel_id.strip()
        and cfg.slack_thread.thread_ts.strip()
    )


async def _slack_disposition_required() -> bool | None:
    try:
        cfg = RunConfig.from_config(get_config())
    except RuntimeError:
        return False
    if cfg.stop_summary is True:
        return True
    if not _configured_slack_run(cfg):
        return False
    configured_slack = cfg.slack_thread
    if configured_slack is None:
        return False
    if not cfg.thread_id:
        return True
    try:
        thread = await langgraph_client().threads.get(cfg.thread_id)
    except Exception:
        logger.warning("Could not recheck active response surface", exc_info=True)
        return None
    metadata = thread_metadata(thread)
    if not metadata:
        return None
    if metadata.get("source") != "slack":
        return False
    location = SourceContext.from_metadata(metadata).slack_location
    if location is None:
        return None
    return location == configured_slack.location


def _is_successful_disposition(message: ToolMessage, invocation_id: str) -> bool:
    if message.name not in _FINAL_TOOLS or message.status == "error":
        return False
    payload = _payload(message)
    if payload is None or payload.get("success") is not True:
        return False
    return (
        payload.get("response_type") == "final"
        and payload.get("open_swe_invocation_id") == invocation_id
    )


def _current_invocation_messages(messages: Sequence[BaseMessage]) -> Sequence[BaseMessage]:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index:]
    return messages


def _has_disposition(messages: Sequence[BaseMessage], invocation_id: str) -> bool:
    return any(
        isinstance(message, ToolMessage) and _is_successful_disposition(message, invocation_id)
        for message in _current_invocation_messages(messages)
    )


def _append_instruction(message: SystemMessage | None) -> SystemMessage:
    existing = message.text if message is not None else ""
    return SystemMessage(content=f"{existing}\n\n{_REPAIR_PROMPT}" if existing else _REPAIR_PROMPT)


def _raise_missing(invocation_id: str | None) -> Never:
    logger.error(
        "Slack response disposition repair exhausted",
        extra={"slack_response_disposition": {"invocation_id": invocation_id}},
    )
    raise MissingSlackResponseDispositionError(
        "Slack run ended without a successful final slack_thread_reply or "
        "no_slack_response_needed disposition"
    )


class SlackResponseDispositionMiddleware(OpenSWEMiddleware):
    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelResponse[object]:
        response = await handler(request)
        if _has_tool_calls(response):
            return response
        required = await _slack_disposition_required()
        if required is not True:
            if required is None:
                _raise_missing(_invocation_id())
            return response
        messages = [*request.messages, *response.result]
        invocation_id = _invocation_id()
        if invocation_id is None:
            _raise_missing(None)
        if _has_disposition(messages, invocation_id):
            return response

        attempts_used = _repair_attempts(messages, invocation_id)
        if attempts_used >= _MAX_REPAIR_ATTEMPTS:
            _raise_missing(invocation_id)
        repair_tools = [tool for tool in request.tools if _tool_name(tool) in _FINAL_TOOLS]
        if not repair_tools:
            _raise_missing(invocation_id)
        for attempt in range(attempts_used + 1, _MAX_REPAIR_ATTEMPTS + 1):
            response = await handler(
                request.override(
                    messages=cast(list[AnyMessage], messages),
                    system_message=_append_instruction(request.system_message),
                    tools=repair_tools,
                    tool_choice="any",
                )
            )
            _tag_repair(response, invocation_id, attempt)
            required = await _slack_disposition_required()
            if required is False or _has_tool_calls(response):
                return response
            if required is None:
                _raise_missing(invocation_id)
            messages.extend(response.result)
        _raise_missing(invocation_id)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | ToolCommand]],
    ) -> ToolMessage | ToolCommand:
        name = request.tool_call["name"]
        if name not in _FINAL_TOOLS:
            return await handler(request)
        required = await _slack_disposition_required()
        if required is not True:
            content = {
                "success": False,
                "error": _SURFACE_CHANGED_ERROR if required is False else _SURFACE_UNKNOWN_ERROR,
            }
            return ToolMessage(
                content=json.dumps(content),
                artifact=content,
                name=name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result
        payload = _payload(result)
        invocation_id = _invocation_id()
        if payload is None or invocation_id is None:
            return result
        args = request.tool_call.get("args")
        response_type = (
            "final"
            if name == "no_slack_response_needed"
            else args.get("response_type")
            if isinstance(args, Mapping)
            else None
        )
        content = {
            **payload,
            "response_type": response_type,
            "open_swe_invocation_id": invocation_id,
        }
        return result.model_copy(update={"content": json.dumps(content), "artifact": content})
