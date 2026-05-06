from typing import Literal, TypedDict, Unpack

import anthropic
import openai
from langchain.chat_models import init_chat_model

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"


OpenAIReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]


class OpenAIReasoning(TypedDict, total=False):
    effort: OpenAIReasoningEffort


class ModelKwargs(TypedDict, total=False):
    max_tokens: int | None
    reasoning: OpenAIReasoning | None
    temperature: float | None


# Transient provider errors we want to retry on. Anthropic surfaces 5xx (529
# Overloaded, 503 Service Unavailable, 502 Bad Gateway) and 429 Rate Limit as
# subclasses of ``APIStatusError``; ``APIConnectionError`` covers transport
# failures (DNS, TLS, dropped sockets). The OpenAI client mirrors the same
# surface. Without retrying, a single 529 anywhere in the trace propagates up
# the middleware chain and terminates the whole agent run silently.
_RETRYABLE_PROVIDER_ERRORS: tuple[type[BaseException], ...] = (
    anthropic.APIStatusError,
    anthropic.APIConnectionError,
    openai.APIStatusError,
    openai.APIConnectionError,
)


def make_model(model_id: str, **kwargs: Unpack[ModelKwargs]):
    model_kwargs: dict[str, object] = kwargs.copy()

    if model_id.startswith("openai:"):
        model_kwargs["base_url"] = OPENAI_RESPONSES_WS_BASE_URL
        model_kwargs["use_responses_api"] = True

    model = init_chat_model(model=model_id, **model_kwargs)
    return model.with_retry(
        retry_if_exception_type=_RETRYABLE_PROVIDER_ERRORS,
        wait_exponential_jitter=True,
        stop_after_attempt=4,
    )
