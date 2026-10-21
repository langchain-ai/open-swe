"""Tool errors as results, and a dead sandbox reported on the channel the run came from."""

import logging
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from osweshared.tool_error_handler import BaseToolErrorMiddleware

from openswe.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification

logger = logging.getLogger(__name__)


def _get_run_config(request: ToolCallRequest) -> Mapping[str, Any] | None:
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    if isinstance(runtime_config, Mapping):
        return runtime_config
    try:
        maybe_config = get_config()
    except Exception:
        logger.exception("Failed to read runnable config while handling sandbox error")
        return None
    return maybe_config if isinstance(maybe_config, Mapping) else None


class ToolErrorMiddleware(BaseToolErrorMiddleware):
    async def anotify_sandbox_unreachable(
        self, request: ToolCallRequest, sandbox_id: str | None
    ) -> None:
        config = _get_run_config(request)
        if config is not None:
            await post_sandbox_unreachable_notification(config, sandbox_id=sandbox_id)
