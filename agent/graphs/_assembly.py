"""The pieces every graph factory in this package assembles from.

Config isolation, the placeholder graph, the deferred-model fallback, the
gateway lookup and the ZDR-gate-then-provider-kwargs step were copied into each
factory and drifted apart; they live here once.
"""

import logging
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

from ..models.deferred import make_deferred_error_model
from ..models.factory import ModelKwargs, OpenAIReasoning, make_model, provider_model_kwargs
from ..runtime.constants import DEFAULT_LLM_MAX_TOKENS, DEFAULT_RECURSION_LIMIT
from ..settings.options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    gate_fable_model,
    model_supports_effort,
)
from ..settings.team_settings import get_effective_gateway_enabled
from ..utils import ttl_cache

logger = logging.getLogger(__name__)


def prepare_config(
    config: RunnableConfig, *, share_configurable: bool = False
) -> tuple[RunnableConfig, dict[str, Any]]:
    """Return the config to build this run's graph from, plus its ``configurable``.

    The caller's mapping is left alone — the runtime owns it and hands it to
    every factory call — so the recursion limit goes onto a copy. It is assigned
    rather than ``setdefault``-ed because the runtime always supplies its own,
    much smaller default, which would otherwise silently cap the graph.

    ``share_configurable`` keeps the caller's ``configurable`` mapping instead of
    copying it, for the one factory (the main agent) whose resolved values —
    ``draft_prs`` — the caller has to see.
    """
    prepared = config.copy()
    source = config.get("configurable") or {}
    configurable = source if share_configurable else dict(source)
    prepared["configurable"] = configurable
    prepared["recursion_limit"] = DEFAULT_RECURSION_LIMIT
    return prepared, configurable


def stub_agent(config: RunnableConfig) -> Pregel:
    """The placeholder graph for a call that is not a real run.

    The runtime also invokes factories to introspect a graph — schema fetches,
    Studio — with no thread id. Building the real agent then would create a
    sandbox and load integrations for nobody.
    """
    return create_deep_agent(system_prompt="", tools=[]).with_config(config)


def make_model_or_defer(model_id: str, *, use_gateway: bool, **kwargs: Any) -> BaseChatModel:
    """Build the model, or one that raises the setup error on first use.

    A factory that raises kills the run before anything can tell the user why;
    deferring turns it into an ordinary model error inside the run.
    """
    try:
        return make_model(model_id, use_gateway=use_gateway, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deferring model setup failure for %s", model_id, exc_info=True)
        return make_deferred_error_model(e, model_id=model_id)


@dataclass(frozen=True)
class ModelSpec:
    """A resolved model id and effort, plus the provider kwargs they build with."""

    model_id: str
    effort: str | None
    kwargs: ModelKwargs

    def build(self, *, use_gateway: bool) -> BaseChatModel:
        return make_model_or_defer(self.model_id, use_gateway=use_gateway, **self.kwargs)


def model_spec(
    model_id: str,
    effort: str | None,
    *,
    fable_enabled: bool = True,
    max_tokens: int = DEFAULT_LLM_MAX_TOKENS,
    openai_reasoning_default: OpenAIReasoning | None = None,
) -> ModelSpec:
    """Gate the pair, then derive the provider kwargs for it.

    ``fable_enabled=False`` swaps a Fable id for a non-Fable one (the ZDR gate);
    pass the team setting whenever the id came from settings rather than from a
    constant in this repository.
    """
    gated_id, gated_effort = gate_fable_model(model_id, effort, fable_enabled=fable_enabled)
    return ModelSpec(
        gated_id,
        gated_effort,
        provider_model_kwargs(
            gated_id,
            gated_effort,
            max_tokens=max_tokens,
            openai_reasoning_default=openai_reasoning_default,
        ),
    )


def requested_model_pair(model_id: object, effort: object) -> tuple[str, str] | None:
    """The ``(model, effort)`` a run explicitly asked for, or ``None`` if unusable.

    A supported pair is taken as-is; a deprecated id is replaced by the pair it
    was renamed to. Anything else is ignored so a stale or hand-edited config
    falls through to the team default instead of failing the run.
    """
    if (
        isinstance(model_id, str)
        and model_id in SUPPORTED_MODEL_IDS
        and isinstance(effort, str)
        and model_supports_effort(model_id, effort)
    ):
        return model_id, effort
    return canonical_model_pair(model_id, effort)


async def cached_gateway_enabled() -> bool:
    """Whether runs route through the LangSmith LLM Gateway.

    Cached briefly so graph factories stay off the settings store during worker
    load and retry storms.
    """
    return await ttl_cache.cached("team:gateway-enabled", 60, get_effective_gateway_enabled)
