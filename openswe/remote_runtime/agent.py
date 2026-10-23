"""The coding agent, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig
from runtime_api.catalog import ModelSpec, RunModels

from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.remote_runtime.graph import RemoteGraph
from openswe.run_config import RunConfig
from openswe.runtime.constants import DEFAULT_LLM_MAX_TOKENS
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.utils.model import provider_model_kwargs

ASSISTANT_ID: Final = "agent"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    from openswe.server import plan_agent

    await plan_agent(config, surface)


def _spec(model_id: str, effort: str | None) -> ModelSpec:
    kwargs = provider_model_kwargs(model_id, effort, max_tokens=DEFAULT_LLM_MAX_TOKENS)
    return ModelSpec.model_validate({"model_id": model_id, "kwargs": dict(kwargs)})


async def run_models(cfg: RunConfig) -> RunModels:
    """The models a run's graph is built with until its catalog names the factory's."""
    settings = await cached_workspace_settings(cfg.workspace_slug)
    (model_id, effort), (subagent_id, subagent_effort) = settings.default_model_pair("agent")
    return RunModels(
        model=_spec(model_id, effort),
        subagent_model=_spec(subagent_id, subagent_effort),
        use_gateway=settings.effective_gateway_enabled,
    )


AGENT: Final = RemoteGraph(ASSISTANT_ID, _plan, run_models)
