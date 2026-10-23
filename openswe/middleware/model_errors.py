"""Observability for failed model calls: full logs, plus the ``ModelError`` the reply reads."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langgraph.config import get_config

from openswe.middleware.trace import OpenSWEMiddleware
from openswe.model_failures import ModelError
from openswe.utils.errors import classify_exception, error_tracking_fields, exception_fields

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


class ModelErrorMiddleware(OpenSWEMiddleware):
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
            thread_id, run_id = _run_context()
            if thread_id is not None and code is not None:
                await ModelError(run_id=run_id, error_type=type(exc).__name__, code=code).record(
                    thread_id
                )
            raise
