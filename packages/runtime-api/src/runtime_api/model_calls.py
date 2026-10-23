"""What the runtime tells the backend about the model calls it makes."""

from typing import Literal

from pydantic import BaseModel

from runtime_api.endpoint import Endpoint
from runtime_api.operations import RunRequest

type ModelFailureCode = Literal[
    "provider_overloaded",
    "provider_rate_limited",
    "provider_unavailable",
    "provider_timeout",
    "context_too_long",
    "model_unavailable",
]


class ModelFailure(RunRequest):
    """A model call that failed at its provider, classified while the error was intact.

    The run then ends with that error, whose class name is all its end reports; the
    backend keeps this for the reply that tells the person why.
    """

    code: ModelFailureCode
    # The exception's class name, as the run's end reports it.
    error_type: str
    # provider:model, when the call named its model.
    model_id: str | None = None
    # Whether the call went through the LangSmith Gateway, which can fail it too.
    via_gateway: bool = False


class Recorded(BaseModel):
    """The backend kept what it was told."""


MODEL_FAILURE = Endpoint("model_failure", ModelFailure, Recorded)
