"""Observability for failed model calls: full logs, plus a reason the reply can use.

``ModelFallbackMiddleware`` is only attached when a fallback model is configured,
and it logs exception class names rather than provider bodies — so a model error
can end a run leaving nothing behind but the platform's own traceback.

The run-completion webhook only ever sees the exception's class name (the platform
scrubs the message of any type outside its allowlist), so the classification that
distinguishes "provider overloaded" from every other ``APIError`` has to be made
here, while the exception is intact, and left on the thread for the reply to read.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langgraph.config import get_config
from langgraph_sdk import get_client

from agent.utils.errors import (
    LAST_MODEL_ERROR_KEY,
    classify_exception,
    error_tracking_fields,
    exception_fields,
)

logger = logging.getLogger(__name__)


def _model_name(model: Any) -> str:
    return getattr(model, "model_name", None) or getattr(model, "model", None) or "unknown"


def _run_context() -> tuple[str | None, str | None]:
    try:
        config = get_config()
    except Exception:  # noqa: BLE001
        return None, None
    if not isinstance(config, Mapping):
        return None, None
    configurable = config.get("configurable")
    configurable = configurable if isinstance(configurable, Mapping) else {}
    thread_id = configurable.get("thread_id")
    run_id = config.get("run_id") or configurable.get("run_id")
    return (
        thread_id if isinstance(thread_id, str) and thread_id else None,
        str(run_id) if run_id else None,
    )


class ModelErrorMiddleware(AgentMiddleware):
    """Log a model-call exception in full and record why, then re-raise it unchanged."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            return await handler(request)
        except Exception as exc:
            model = _model_name(request.model)
            fields = exception_fields(exc)
            code = classify_exception(exc)
            logger.exception(
                "Model call failed",
                extra={
                    **error_tracking_fields(exc),
                    "model_call_failure": {"model": model, "code": code, **fields},
                },
            )
            await _record_model_error(exc, code)
            raise


async def _record_model_error(exc: BaseException, code: str | None) -> None:
    """Leave the classification on the thread for the run-completion reply."""
    thread_id, run_id = _run_context()
    if thread_id is None or code is None:
        return
    try:
        await get_client().threads.update(
            thread_id=thread_id,
            metadata={
                LAST_MODEL_ERROR_KEY: {
                    "run_id": run_id,
                    "error_type": type(exc).__name__,
                    "code": code,
                }
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to record model error for thread %s", thread_id)
