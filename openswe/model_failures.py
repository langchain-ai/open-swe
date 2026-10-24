"""Why a run's model call failed, kept on its thread for the run-completion reply.

The run-completion webhook only ever sees the exception's class name (the platform
scrubs the message of any type outside its allowlist), so the classification that
distinguishes "provider overloaded" from every other ``APIError``, and the provider
that failed, is made while the exception is intact and left on the thread. An
in-process run makes it in ``ModelErrorMiddleware``; an MDA run makes it where its
model call failed and hands it over through ``runtime_api``'s ``MODEL_FAILURE``.
"""

import logging
from collections.abc import Mapping
from typing import Final, Self

from pydantic import BaseModel, ValidationError

from openswe.threads import index

logger = logging.getLogger(__name__)

# Thread-metadata key holding the run's last model error.
LAST_MODEL_ERROR_KEY: Final = "last_model_error"
_PROVIDER_NAMES: Final = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google_genai": "Google",
    "fireworks": "Fireworks",
    "baseten": "Baseten",
    "groq": "Groq",
}


class ModelError(BaseModel):
    """Why a run's model call failed, as the run-completion reply reads it."""

    run_id: str | None
    error_type: str
    code: str
    # provider:model, when the failing side knew which model it called.
    model_id: str | None = None
    # The call went through the LangSmith Gateway, which may be what failed it.
    via_gateway: bool = False

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
        """Who failed the call, as the reply names it; None when nothing names them."""
        provider, _, name = (self.model_id or "").partition(":")
        named = _PROVIDER_NAMES.get(provider) if name else None
        if not self.via_gateway:
            return named
        return f"{named or 'the model provider'} or the LangSmith Gateway in front of it"

    async def record(self, thread_id: str) -> None:
        """Leave this on the thread for the run-completion reply."""
        try:
            await index.update(thread_id, {LAST_MODEL_ERROR_KEY: self.model_dump()})
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record model error", extra={"thread_id": thread_id})
