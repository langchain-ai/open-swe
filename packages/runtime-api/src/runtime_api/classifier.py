"""Jev, the LangSmith LLM gateway's classifier: one choice per question about a task.

Both sides ask it: the backend to read a model request and pick reviewers, the runtime
to route a thread (``mda/rt/routing.py``). A key from the environment authenticates it,
as the run's models are (``runtime_api.models``); without one, or when it fails or is
unsure, it chooses nothing and ``JevDecision`` says why.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal, Self

import httpx2
from langchain_typesafe import Choice, TypeSafeClassifier
from langchain_typesafe.client import TypeSafePermissionDeniedError

from runtime_api.models import gateway_api_key, gateway_base_url

logger = logging.getLogger(__name__)

JEV_TIMEOUT_SECONDS = 3.0
# Pinned so confidence thresholds tuned against it keep holding.
SYSTEM_ONE_MODEL = "typesafe/jev-1.13.0"


@dataclass(frozen=True)
class SystemOneGateway:
    """TypeSafe's System One model, reached through the LangSmith LLM Gateway."""

    api_key: str

    @classmethod
    def configured(cls) -> Self | None:
        """The gateway this deployment's LangSmith key reaches, or None without one."""
        api_key = gateway_api_key()
        return cls(api_key) if api_key else None

    def classifier(self, client: httpx2.AsyncClient) -> TypeSafeClassifier:
        return TypeSafeClassifier(
            model=SYSTEM_ONE_MODEL,
            api_key=self.api_key,
            base_url=gateway_base_url(),
            async_client=client,
        )


@dataclass
class JevDecision:
    choice: str | None = None
    confidence: float | None = None
    outcome: Literal["not_run", "accepted", "low_confidence", "classifier_failure"] = "not_run"
    reason: str = "not_run"


async def select_jev_choice[ChoiceT: str](
    task: str,
    *,
    question: str,
    instructions: str,
    criteria: Mapping[ChoiceT, str],
    decision: JevDecision | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> ChoiceT | None:
    choices = await select_jev_choices(
        task,
        questions={question: Choice(instructions=instructions, criteria=dict(criteria.items()))},
        decisions={question: decision},
        transport=transport,
    )
    return next((choice for choice in criteria if choice == choices[question]), None)


async def select_jev_choices(
    task: str,
    *,
    questions: Mapping[str, Choice],
    decisions: Mapping[str, JevDecision | None] | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> dict[str, str | None]:
    results: dict[str, str | None] = dict.fromkeys(questions)
    tracked = {question: (decisions or {}).get(question) or JevDecision() for question in questions}
    for decision in tracked.values():
        decision.outcome = "classifier_failure"
        decision.reason = "missing_credentials"
    gateway = SystemOneGateway.configured()
    if gateway is None:
        logger.warning("Jev classification has no API key", extra={"questions": list(questions)})
        return results
    for decision in tracked.values():
        decision.reason = "classifier_error"
    try:
        async with (
            asyncio.timeout(JEV_TIMEOUT_SECONDS),
            httpx2.AsyncClient(transport=transport, timeout=JEV_TIMEOUT_SECONDS) as client,
        ):
            response = await gateway.classifier(client).ainvoke(
                {"state": task, "questions": dict(questions)},
                config={"tags": ["nostream"]},
            )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(exc, TypeSafePermissionDeniedError) or status in (401, 403):
            for decision in tracked.values():
                decision.reason = "classifier_auth_failure"
            logger.error(
                "Jev classifier credential rejected",
                extra={
                    "questions": list(questions),
                    "status_code": status,
                    "error_type": type(exc).__name__,
                },
            )
        else:
            logger.exception("Jev classification failed", extra={"questions": list(questions)})
        return results
    for question, spec in questions.items():
        decision = tracked[question]
        try:
            answer = response.choices[question]
            decision.choice = answer.choice if answer.choice in spec.criteria else None
            decision.confidence = answer.confidence if isfinite(answer.confidence) else None
            if not 0.6 <= answer.confidence <= 1.0:
                logger.info(
                    "Jev classification confidence below threshold or invalid",
                    extra={"question": question, "confidence": answer.confidence},
                )
                decision.outcome = "low_confidence"
                decision.reason = "confidence_below_threshold_or_invalid"
            elif answer.choice in spec.criteria:
                decision.outcome = "accepted"
                decision.reason = "confident_choice"
                results[question] = answer.choice
            else:
                decision.reason = "invalid_choice"
                raise ValueError("Jev returned a choice outside the supplied criteria")
        except Exception:
            logger.exception("Jev classification failed", extra={"question": question})
    return results
