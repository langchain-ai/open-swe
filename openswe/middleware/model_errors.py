"""Observability for failed model calls: full logs, plus a reason the reply can use.

The run-completion webhook only ever sees the exception's class name (the platform
scrubs the message of any type outside its allowlist), so the classification that
distinguishes "provider overloaded" from every other ``APIError``, and the provider
that failed, has to be made while the exception is intact and left on the thread
for the reply to read. An in-process run makes it here; an MDA run makes it where
its model call failed and hands it over through the ``model_error`` hook.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, Self

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langgraph.config import get_config
from pydantic import BaseModel, ValidationError

from openswe.middleware.trace import OpenSWEMiddleware
from openswe.utils.errors import classify_exception, error_tracking_fields, exception_fields
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

# Thread-metadata key holding the run's last model error.
LAST_MODEL_ERROR_KEY: Final = "last_model_error"
_PROVIDER_NAMES: Final = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google_genai": "Google",
    "fireworks": "Fireworks",
    "baseten": "Baseten",
}


class ModelError(BaseModel):
    """Why a run's model call failed, as the run-completion reply reads it."""

    run_id: str | None
    error_type: str
    code: str
    # provider:model, when the failing side knew which model it called.
    model_id: str | None = None

    @classmethod
    def last_on(cls, metadata: Mapping[str, object]) -> Self | None:
        """The model error recorded in a thread's metadata, if any."""
        raw = metadata.get(LAST_MODEL_ERROR_KEY)
        if not isinstance(raw, Mapping):
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            logger.warning("Ignoring a malformed recorded model error", exc_info=True)
            return None

    @property
    def provider(self) -> str | None:
        """The provider's display name, or None when the model or provider is unknown."""
        if self.model_id is None:
            return None
        provider, _, name = self.model_id.partition(":")
        return _PROVIDER_NAMES.get(provider) if name else None

    async def record(self, thread_id: str) -> None:
        """Leave this on the thread for the run-completion reply."""
        try:
            await langgraph_client().threads.update(
                thread_id=thread_id, metadata={LAST_MODEL_ERROR_KEY: self.model_dump()}
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record model error", extra={"thread_id": thread_id})


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
