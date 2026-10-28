"""A visual assessment's minimal MDA catalog, independent of the code reviewer plan."""

from uuid import UUID

from runtime_api.catalog import Catalog, CliResultRules, LoopRules, RunModels

from openswe.agent_api.loop import model_spec
from openswe.agent_api.tokens import RemoteRun
from openswe.dashboard.options import default_vision_model_pair, model_supports_images
from openswe.prompts import prompt
from openswe.reviewer import resolve_reviewer_models
from openswe.run_config import RunConfig
from openswe.runtime.constants import DEFAULT_LLM_MAX_TOKENS
from openswe.sandboxes.tool_runtime import FILESYSTEM_TOOL_NAMES
from openswe.utils.model import provider_model_kwargs

from .schemas import FrontendReport
from .service import get_review, set_assessment_state

ALLOWED_ENDPOINTS = frozenset(
    {
        "prepare",
        "reports",
        "model_result",
        "tool_started",
        "tool_result",
        "run_ended",
        "step_limit_reached",
        "model_choice",
        "read_frontend_review",
        "submit_frontend_review",
    }
)


async def catalog_for(run: RemoteRun) -> Catalog:
    cfg = RunConfig.parse(run.configurable)
    if (
        run.assistant_id != "reviewer"
        or cfg.repo is None
        or cfg.pr_number is None
        or cfg.head_sha is None
        or cfg.frontend_review_id is None
    ):
        raise ValueError("A frontend assessment needs its frozen PR and evidence identity")
    UUID(cfg.frontend_review_id)
    models = await resolve_reviewer_models(cfg)
    selected = model_spec(models.main.model_id, models.main.kwargs)
    vision = None
    if not model_supports_images(selected.model_id):
        vision_id, effort = default_vision_model_pair()
        vision = model_spec(
            vision_id, provider_model_kwargs(vision_id, effort, max_tokens=DEFAULT_LLM_MAX_TOKENS)
        )
    return Catalog(
        system_prompt=prompt("frontend-review/system"),
        code_interpreter=False,
        frontend_inspect_description=prompt("tools/read_frontend_review"),
        frontend_assessment_description=prompt("tools/submit_frontend_review"),
        models=RunModels(
            model=selected, subagent_model=selected, use_gateway=models.use_gateway, vision=vision
        ),
        excluded=[
            *FILESYSTEM_TOOL_NAMES,
            "delete",
            "write_todos",
            "task",
            "eval",
            "start_async_task",
            "check_async_task",
            "update_async_task",
            "cancel_async_task",
            "list_async_tasks",
        ],
        model_call_limit=8,
        loop=LoopRules(
            stop_when=["frontend_review_complete"],
            cli_result=CliResultRules(
                tool="submit_frontend_review",
                nudge=[prompt("frontend-review/submit-nudge")],
                max_nudges=1,
            ),
        ),
    )


async def assessment_ended(run: RemoteRun) -> None:
    cfg = RunConfig.parse(run.configurable)
    if cfg.repo is None or cfg.pr_number is None or cfg.frontend_review_id is None:
        return
    review = await get_review(
        cfg.repo.owner, cfg.repo.name, cfg.pr_number, UUID(cfg.frontend_review_id)
    )
    if review is not None and review.report:
        report = FrontendReport.model_validate(review.report)
        if report.ai_review_status != "complete":
            await set_assessment_state(
                review,
                "failed",
                error="The AI run ended without publishing an assessment. Captured evidence remains available.",
            )
