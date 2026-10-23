"""The review scout, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import ModelSpec, RunModels

from openswe.remote_runtime.graph import RemoteGraph
from openswe.review_scout.graph import plan_review_scout, scout_models
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ModelChoice, ToolSurface

ASSISTANT_ID: Final = "review-scout"

_json = TypeAdapter(dict[str, JsonValue])


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    await plan_review_scout(config, surface)


def _model_spec(choice: ModelChoice) -> ModelSpec:
    return ModelSpec(model_id=choice.model_id, kwargs=_json.validate_python(dict(choice.kwargs)))


async def run_models(cfg: RunConfig) -> RunModels:
    models = await scout_models(cfg)
    return RunModels(
        model=_model_spec(models.main),
        subagent_model=_model_spec(models.subagent),
        use_gateway=models.use_gateway,
    )


REVIEW_SCOUT: Final = RemoteGraph(ASSISTANT_ID, _plan, run_models)
