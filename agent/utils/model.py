import os
from typing import Literal, TypedDict, Unpack

from langchain.chat_models import init_chat_model

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"

DEFAULT_MODEL_ID = "openai:gpt-5.5"


OpenAIReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]


class OpenAIReasoning(TypedDict, total=False):
    effort: OpenAIReasoningEffort


class ModelKwargs(TypedDict, total=False):
    max_tokens: int | None
    reasoning: OpenAIReasoning | None
    temperature: float | None


def get_model_id() -> str:
    """Return the LLM model identifier, resolved from environment variables.

    Resolution order:
      1. ``AGENT_MODEL`` — the primary override (recommended).
      2. ``LLM_MODEL_ID`` — legacy env var, kept for backwards compatibility.
      3. ``DEFAULT_MODEL_ID`` — the built-in default.
    """
    return os.environ.get("AGENT_MODEL") or os.environ.get("LLM_MODEL_ID", DEFAULT_MODEL_ID)


def make_model(model_id: str, **kwargs: Unpack[ModelKwargs]):
    model_kwargs: dict[str, object] = kwargs.copy()

    if model_id.startswith("openai:"):
        model_kwargs["base_url"] = OPENAI_RESPONSES_WS_BASE_URL
        model_kwargs["use_responses_api"] = True

    return init_chat_model(model=model_id, **model_kwargs)
