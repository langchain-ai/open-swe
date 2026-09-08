import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, NotRequired

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

Route = Literal["sol_medium", "terra_high", "luna_xhigh"]

_CLASSIFIER_PROMPT = """Route one Open SWE human turn to one fixed model profile. The selected model remains pinned for every parent-agent model call in this turn to preserve provider prompt-cache reuse. This router does not change the subagent model or switch models between design, implementation, and review inside one turn.

Profiles, from least to most capable and expensive:

1. luna_xhigh
- Use for direct lookup, extraction, status checks, test or log collection, mechanical PR or release operations, and localized changes with explicit targets and strong verification.
- Xhigh effort increases persistence, not the base model's capability ceiling.

2. terra_high
- Use for ordinary bug fixes, bounded investigations, multi-file implementation, research synthesis, semantic PR maintenance, and partially specified localized work.

3. sol_medium
- Use for architecture or design, requirements disambiguation, subtle semantic review, novel root-cause reasoning, conflicting evidence, cross-component or multi-repository judgment, and high-stakes decisions.
- Medium is its configured reasoning effort; it remains the highest-capability profile.

Choose the least expensive profile likely to complete the whole current turn safely. Prompt length and eventual runtime are not difficulty signals. Explicit file or symbol targets, clear acceptance criteria, reversibility, and strong tests lower the required capability. Missing reproductions, unclear ownership, weak tests, architectural tradeoffs, broad scope, and conflicting assumptions raise it.

Security, authentication, authorization, secrets, production changes, migrations, destructive operations, and weakly reversible data work require sol_medium when this turn must design, diagnose, or review the risky behavior. A tightly specified, reversible implementation with strong verification may use terra_high, but not luna_xhigh merely because the diff is small.

Broad research is usually decomposition-heavy rather than intrinsically a sol_medium task. Use luna_xhigh when this turn mainly gathers independent evidence, terra_high when it must synthesize a bounded body of evidence, and sol_medium only when synthesis is consequential, conflicting, or deeply sequential. Mark parallel or hybrid decomposition when independent subagent fanout would help, but do not assume this router controls worker models.

Use these observed Open SWE workload priors only as tie-breakers, not hard rules: feature changes and bug fixes are the largest interactive categories and typically require several agent invocations; direct questions and routine operations are usually shorter; design, review, and investigation have long upper tails. Classify the request itself rather than predicting a percentile bucket.

Escalate luna_xhigh to terra_high if the named scope expands, instructions prove incomplete, or verification fails unexpectedly. Escalate terra_high to sol_medium if architecture must be invented, assumptions conflict, root cause remains unclear, or risk becomes material.

Current human turn:
{task}
"""


class RouteSignals(BaseModel):
    scope: Literal[
        "answer_only", "localized", "multi_file", "multi_component", "multi_repo", "unknown"
    ]
    decomposition: Literal["none", "parallel", "sequential", "hybrid"]
    specification: Literal["clear", "partial", "ambiguous"]
    verification: Literal["strong", "partial", "weak", "unknown"]
    risks: list[
        Literal[
            "security",
            "auth",
            "secrets",
            "production",
            "migration",
            "data",
            "destructive",
            "external_api",
        ]
    ]
    design_needed: bool
    review_needed: bool


class RouteDecision(BaseModel):
    task_category: Literal[
        "question_lookup",
        "research",
        "scoping_design",
        "bug_fix",
        "investigation_diagnosis",
        "feature_change",
        "review",
        "pr_maintenance",
        "release_operations",
        "admin_communication",
        "test_noop",
        "other",
    ]
    initial_route: Route
    signals: RouteSignals
    escalation_conditions: list[str]
    reason: str
    confidence: float = Field(ge=0, le=1)


class ModelSelectionState(AgentState):
    model_route: NotRequired[Route]
    model_route_for: NotRequired[str]
    model_route_plan: NotRequired[dict[str, Any]]


def _message_text(message: HumanMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return " ".join(
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def _latest_task(state: Mapping[str, Any]) -> tuple[str, str]:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return "", ""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage):
            task = _message_text(message)
            payload = {"index": index, "id": message.id, "task": task}
            fingerprint = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            return task, fingerprint
    return "", ""


class ModelSelectionMiddleware(AgentMiddleware[ModelSelectionState]):
    state_schema = ModelSelectionState

    def __init__(
        self,
        models: Mapping[str, BaseChatModel],
        classifier: BaseChatModel,
        *,
        initial_plan_mode: bool = False,
    ) -> None:
        self._models = dict(models)
        self._classifier = classifier.with_structured_output(RouteDecision)
        self._initial_plan_mode = initial_plan_mode

    async def _classify(self, task: str) -> RouteDecision | None:
        if self._initial_plan_mode:
            return None
        try:
            decision = await self._classifier.ainvoke(_CLASSIFIER_PROMPT.format(task=task[-8_000:]))
        except Exception:  # noqa: BLE001
            logger.exception("Model routing classifier failed")
            return None
        if not isinstance(decision, RouteDecision):
            logger.warning("Model routing classifier returned an unexpected result")
            return None
        return decision

    async def abefore_agent(
        self,
        state: ModelSelectionState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        task, fingerprint = _latest_task(state)
        if state.get("model_route_for") == fingerprint and state.get("model_route") in self._models:
            return None
        decision = await self._classify(task)
        route: Route = decision.initial_route if decision is not None else "terra_high"
        if self._initial_plan_mode:
            route = "sol_medium"
        return {
            "model_route": route,
            "model_route_for": fingerprint,
            "model_route_plan": decision.model_dump(mode="json") if decision is not None else {},
        }

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        route = request.state.get("model_route")
        if route not in self._models:
            route = "terra_high"
        logger.info("Selected model route", extra={"model_route": route})
        return await handler(request.override(model=self._models[route]))
