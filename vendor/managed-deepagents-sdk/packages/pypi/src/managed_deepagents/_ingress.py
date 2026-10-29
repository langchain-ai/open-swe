"""Shared scheduling, loopback clients, and delivery context for channel ingress."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Config, Context

from managed_deepagents._identity_auth import (
    HANDOFF_SECRET_ENV,
    HANDOFF_SECRET_HEADER,
    INGRESS_SECRET_ENV,
    INGRESS_SECRET_HEADER,
    LANGSMITH_API_KEY_ENV,
    SOURCE_PROVIDER_HEADER,
    SOURCE_THREAD_HEADER,
    USER_HEADER,
)
from managed_deepagents._log import logger
from managed_deepagents._loopback import (
    is_loopback_url,
    resolve_loopback_api_key,
    resolve_loopback_api_url,
)
from managed_deepagents._telemetry.otel import (
    LogAttributes,
    LogContext,
    enter_log_context,
    exit_log_context,
    hold_log_context,
    record_log,
    trace_id_from_traceparent,
)
from managed_deepagents.run_context import ManagedChannelContext

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding


ScheduleFn = Callable[[Callable[[], Awaitable[None]]], None]


def schedule_ingress_work(
    work: Callable[[], Awaitable[None]],
    *,
    label: str,
    schedule: ScheduleFn | None = None,
) -> None:
    """Schedule background work after the provider ACK."""
    if schedule is not None:
        schedule(work)
        return

    async def _run() -> None:
        try:
            await work()
        except Exception as error:  # noqa: BLE001 - logged; must not break the ack path
            logger.error("%s failed (%s)", label, logger.safe_error_label(error), error=error)

    try:
        loop = __import__("asyncio").get_running_loop()
    except RuntimeError:
        __import__("asyncio").run(_run())
        return
    task = loop.create_task(_run())
    hold_log_context(task)
    # Keep a strong ref until the task finishes.
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_background_tasks: set[Any] = set()


async def with_ingress_log_context(
    traceparent: str | None,
    attributes: LogAttributes,
    handle: Callable[[], Awaitable[Any]],
) -> Any:
    """Handle one inbound event inside its own log context.

    The trace id comes from the request's ``traceparent``, or is minted. Once
    the response and any work scheduled through :func:`schedule_ingress_work`
    settle, an ``ingress completed`` record is queued.
    """
    asyncio = __import__("asyncio")
    context = LogContext(trace_id=trace_id_from_traceparent(traceparent))
    context.attributes.update(attributes)
    token = enter_log_context(context)
    status = 500
    try:
        response = await handle()
        status_code = getattr(response, "status_code", None)
        status = status_code if isinstance(status_code, int) else 200
        return response
    finally:
        exit_log_context(token)

        async def _complete() -> None:
            await asyncio.gather(*context.pending, return_exceptions=True)
            token = enter_log_context(context)
            try:
                record_log("INFO", "ingress completed", attributes={"http.status_code": status})
            finally:
                exit_log_context(token)

        task = asyncio.get_running_loop().create_task(_complete())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


def _create_ingress_loopback_client(
    *,
    user_id: str,
    source_thread_key: str,
    source_provider: str = "slack",
) -> LangGraphClient:
    api_url = resolve_loopback_api_url()
    api_key = resolve_loopback_api_key()
    is_local = is_loopback_url(api_url)
    handoff_secret = os.environ.get(HANDOFF_SECRET_ENV, "").strip() if is_local else ""
    server_api_key = os.environ.get(LANGSMITH_API_KEY_ENV, "").strip()
    ingress_secret = os.environ.get(INGRESS_SECRET_ENV, "").strip() if is_local else ""
    headers = {
        USER_HEADER: user_id,
        SOURCE_PROVIDER_HEADER: source_provider,
        SOURCE_THREAD_HEADER: source_thread_key,
    }
    if handoff_secret:
        headers[HANDOFF_SECRET_HEADER] = handoff_secret
    elif not server_api_key and ingress_secret:
        headers[INGRESS_SECRET_HEADER] = ingress_secret
    return get_client(url=api_url, api_key=api_key, headers=headers)


def _has_loopback_credential() -> bool:
    is_local = is_loopback_url(resolve_loopback_api_url())
    return bool(
        os.environ.get(LANGSMITH_API_KEY_ENV, "").strip()
        or (
            is_local
            and (
                os.environ.get(HANDOFF_SECRET_ENV, "").strip()
                or os.environ.get(INGRESS_SECRET_ENV, "").strip()
            )
        )
    )


__all__ = [
    "_create_ingress_loopback_client",
    "_has_loopback_credential",
    "schedule_ingress_work",
]


class IngressRunContext(TypedDict):
    context: Context
    config: NotRequired[Config]


def build_ingress_run_context(
    *,
    binding: ChannelBinding,
    context: Mapping[str, Any] | None = None,
    config: Config | None = None,
) -> IngressRunContext:
    """Send the managed channel schema and ordinary execution options."""
    if context is not None and "mda_channel" in context:
        raise ValueError("context.mda_channel is reserved for managed ingress")
    if config and config.get("configurable"):
        raise ValueError("managed ingress cannot combine context and config.configurable")
    execution: Config = {**config} if config else {}
    execution.pop("configurable", None)
    run_context: dict[str, Any] = {
        **(context or {}),
        "mda_channel": ManagedChannelContext.model_validate({"channel": binding}).model_dump(
            by_alias=True
        )["channel"],
    }
    result: IngressRunContext = {"context": run_context}
    if execution:
        result["config"] = execution
    return result
