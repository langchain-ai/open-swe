"""The pull request reviewer, as Managed Deep Agents runs play it."""

from typing import Final

from langchain_core.runnables import RunnableConfig
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import ModelSpec, RunModels

from openswe.agent_api.graph import RemoteGraph
from openswe.review.diff import compute_diff_line_set
from openswe.reviewer import ReviewerModelChoice, plan_reviewer, resolve_reviewer_models
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface

ASSISTANT_ID: Final = "reviewer"

_json = TypeAdapter(JsonValue)


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    await plan_reviewer(config, surface)


def _restore(state: dict[str, object]) -> None:
    # Sets do not cross to MDA and back; the diff they index does.
    diff_text = state.get("diff_text")
    if isinstance(diff_text, str) and diff_text and "diff_line_set" not in state:
        state["diff_line_set"] = compute_diff_line_set(diff_text)


def _model_spec(choice: ReviewerModelChoice) -> ModelSpec:
    return ModelSpec(model_id=choice.model_id, kwargs=_json.validate_python(dict(choice.kwargs)))


async def run_models(cfg: RunConfig) -> RunModels:
    models = await resolve_reviewer_models(cfg)
    return RunModels(
        model=_model_spec(models.main),
        subagent_model=_model_spec(models.subagent),
        use_gateway=models.use_gateway,
    )


REVIEWER: Final = RemoteGraph(ASSISTANT_ID, _plan, run_models, restore_state=_restore)
