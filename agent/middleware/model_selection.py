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

_CLASSIFIER_PROMPT = """You are the routing planner for Open SWE. Classify the initial request and produce a phase-aware execution plan. Choose only from these fixed profiles:

1. sol_medium
- GPT-5.6 Sol is the flagship and highest-capability base model.
- Use for judgment-heavy work: problem framing, architecture, requirements disambiguation, research strategy and synthesis, novel root-cause reasoning, consequential decisions, and adversarial code or security review.
- Medium is its configured reasoning effort; do not downgrade it merely because another profile says high or xhigh.

2. terra_high
- GPT-5.6 Terra balances intelligence and cost; high effort makes it a deliberate execution model.
- Use for bounded multi-file implementation, ordinary debugging, semantic PR maintenance, and executing a design whose interfaces and acceptance criteria are already clear.

3. luna_xhigh
- GPT-5.6 Luna is the cost-sensitive, high-volume tier.
- Use for clear and repeatable work: classification, extraction, lookup, independent research fanout, log or test collection, mechanical edits, and tightly specified tool workflows.
- Xhigh effort increases persistence, not the base capability ceiling. Never choose Luna over Sol for architecture or nuanced review merely because xhigh is greater than medium.

Route by phase and cognitive role, not one scalar complexity score. Prefer capable models for design and review; cheaper models for bounded implementation and parallel evidence collection. Risk changes required oversight and review; it does not automatically mean the implementer must be the most expensive model.

Phase rules:
- Direct lookup, extraction, status check, or categorization: luna_xhigh.
- Codebase explanation or bounded technical analysis: terra_high; use sol_medium if architectural, ambiguous, or consequential.
- Design or planning from a rough goal, API or schema decisions, and tradeoff analysis: sol_medium.
- Implementation from an approved explicit plan: luna_xhigh if mechanical, localized, and strongly testable; terra_high if bounded multi-file or moderate debugging; sol_medium first if design remains unresolved or debugging requires novel system reasoning.
- Broad research: sol_medium plans the questions, luna_xhigh workers search independently in parallel, and sol_medium synthesizes and resolves conflicts. Straightforward synthesis may use terra_high.
- Review: luna_xhigh for formatting or checklist validation; terra_high for an ordinary bounded diff; sol_medium for subtle correctness, cross-file behavior, architecture, security, auth, data access, migrations, or high blast radius.
- Rebase, dependency, or PR maintenance: luna_xhigh if mechanical; terra_high if conflicts or behavioral decisions are possible.
- Escalate Luna to Terra when instructions are incomplete, tests fail unexpectedly, or edits expand beyond the named scope.
- Escalate Terra to Sol when assumptions conflict, architecture must be invented, root cause remains unclear, or review risk is material.

Use fanout only when subtasks are substantially independent, outputs can follow a shared evidence schema, and synthesis can detect conflicts. Set parallelism to 1 otherwise.

The initial route is the first phase. The selected route will run the entire current turn, so choose the model needed for the hardest judgment the same agent must perform. A request with an already-approved plan should not be routed as design work. Do not use eventual duration or number of turns as inputs, infer difficulty solely from task category or file count, or treat parallelism as a substitute for capable synthesis.

Initial request:
{task}
"""


class RoutePhase(BaseModel):
    phase: Literal[
        "design", "research_worker", "synthesis", "implementation", "verification", "review"
    ]
    route: Route
    parallelism: int = Field(ge=1, le=8)
    deliverable: str


class RouteSignals(BaseModel):
    design_needed: bool
    plan_already_specified: bool
    fanoutable: bool
    mechanical: bool
    novel_reasoning: bool
    review_depth: Literal["none", "checklist", "ordinary", "adversarial"]
    risk: Literal["low", "medium", "high"]


class RouteDecision(BaseModel):
    task_category: Literal[
        "question",
        "research",
        "design",
        "bug_fix",
        "feature",
        "implementation",
        "investigation",
        "review",
        "pr_maintenance",
        "operations",
        "other",
    ]
    work_shape: Literal["single_phase", "staged", "fanout"]
    initial_route: Route
    phase_plan: list[RoutePhase]
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
