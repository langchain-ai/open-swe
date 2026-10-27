"""Why a run's model call failed, kept on its thread for the run-completion reply.

The run-completion webhook only ever sees the exception's class name (the platform
scrubs the message of any type outside its allowlist), so the classification that
distinguishes "provider overloaded" from every other ``APIError``, and the provider
that failed, is made while the exception is intact and left on the thread. An
in-process run makes it in ``ModelErrorMiddleware``; an MDA run makes it where its
model call failed and hands it over through ``runtime_api``'s ``MODEL_FAILURE``.

A provider outage never ends an MDA run by itself: its calls wait for the same
model to recover, and ``MODEL_RECOVERY`` says when they start and stop waiting.
"""

import logging
import uuid
from collections.abc import Mapping
from typing import Final, Self

from pydantic import BaseModel, ValidationError

from openswe.run_config import RunConfig
from openswe.run_source import notify_run_source
from openswe.threads import index
from openswe.transcript.events import JsonObject
from openswe.transcript.turns import note_on_run_turn
from openswe.utils.user_messages import warning

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


_UNNAMED_PROVIDER: Final = "the model provider"
_OUTAGE_TEXT: Final = {
    "provider_overloaded": "is overloaded",
    "provider_rate_limited": "is rate-limiting Open SWE",
    "provider_unavailable": "keeps returning errors",
    "provider_timeout": "isn't answering in time",
}


def _provider_name(model_id: str | None) -> str | None:
    provider, _, name = (model_id or "").partition(":")
    return _PROVIDER_NAMES.get(provider) if name else None


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
        named = _provider_name(self.model_id)
        if not self.via_gateway:
            return named
        return f"{named or _UNNAMED_PROVIDER} or the LangSmith Gateway in front of it"

    async def record(self, thread_id: str) -> None:
        """Leave this on the thread for the run-completion reply."""
        try:
            await index.update(thread_id, {LAST_MODEL_ERROR_KEY: self.model_dump()})
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record model error", extra={"thread_id": thread_id})


class ProviderWait(BaseModel):
    """A run's model calls started or stopped waiting for their provider to recover.

    The dashboard shows the wait on the run's turn until it ends; the person is
    told where the run started the first time it waits, once per run, which the
    turn's notice receipt keeps true across replicas and a run resumed elsewhere.
    """

    run_id: str | None
    waiting: bool
    code: str | None = None
    model_id: str | None = None
    via_gateway: bool = False

    @property
    def message(self) -> str:
        """What the person is told when the run starts waiting."""
        provider = _provider_name(self.model_id) or _UNNAMED_PROVIDER
        if self.via_gateway:
            provider += " (or the LangSmith Gateway in front of it)"
        _, _, model = (self.model_id or "").partition(":")
        same_model = f"on {model}" if model else "on the same model"
        return warning(
            f"{provider[:1].upper()}{provider[1:]} {_OUTAGE_TEXT.get(self.code or '', 'is failing')}, "
            f"so Open SWE is waiting for it to recover and will keep retrying {same_model} "
            "instead of switching models. Your task is saved and picks up where it left off "
            "as soon as the provider answers. Stop the run in Open SWE Web to cancel it."
        )

    async def report(self, config: Mapping[str, object]) -> None:
        """Note the wait on the run's turn, and tell the person when the run first waits."""
        thread_id = RunConfig.from_config(config).thread_id
        if thread_id is None or self.run_id is None:
            logger.warning(
                "A provider wait names no run", extra={"provider_wait": self.model_dump()}
            )
            return
        data: JsonObject = {
            "waiting": self.waiting,
            "code": self.code,
            "model_id": self.model_id,
            "provider": _provider_name(self.model_id),
            "via_gateway": self.via_gateway,
        }
        if self.waiting:
            first = await note_on_run_turn(
                thread_id,
                self.run_id,
                "provider_recovery",
                data,
                command_id=f"provider_recovery:{self.run_id}:announced",
            )
            if first is not False:
                await notify_run_source(config, self.message, what="provider_recovery")
                return
        await note_on_run_turn(
            thread_id, self.run_id, "provider_recovery", data, command_id=str(uuid.uuid7())
        )
