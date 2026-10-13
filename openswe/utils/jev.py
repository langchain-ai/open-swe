import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal

import httpx2
from langchain_typesafe import Choice, TypeSafeClassifier
from langchain_typesafe.client import TypeSafePermissionDeniedError

from openswe.config import ENV
from openswe.utils.gateway import gateway_base_url

logger = logging.getLogger(__name__)

JEV_TIMEOUT_SECONDS = 3.0


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
) -> ChoiceT | None:
    choices = await select_jev_choices(
        task,
        questions={question: Choice(instructions=instructions, criteria=dict(criteria.items()))},
        decisions={question: decision},
    )
    return next((choice for choice in criteria if choice == choices[question]), None)


async def select_jev_choices(
    task: str,
    *,
    questions: Mapping[str, Choice],
    decisions: Mapping[str, JevDecision | None] | None = None,
) -> dict[str, str | None]:
    results: dict[str, str | None] = dict.fromkeys(questions)
    tracked = {question: (decisions or {}).get(question) or JevDecision() for question in questions}
    for decision in tracked.values():
        decision.outcome = "classifier_failure"
        decision.reason = "missing_credentials"
    typesafe_key = ENV.TYPESAFE_API_KEY.optional()
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()
    if not typesafe_key and not gateway_key:
        logger.warning("Jev classification has no API key", extra={"questions": list(questions)})
        return results
    for decision in tracked.values():
        decision.reason = "classifier_error"
    for attempt in range(2):
        use_gateway = not typesafe_key or attempt == 1
        try:
            async with (
                asyncio.timeout(JEV_TIMEOUT_SECONDS),
                httpx2.AsyncClient(timeout=JEV_TIMEOUT_SECONDS) as client,
            ):
                classifier = TypeSafeClassifier(
                    model="typesafe/jev-1.13.0" if use_gateway else "jev-1.13.0",
                    api_key=gateway_key if use_gateway else typesafe_key,
                    **({"base_url": gateway_base_url()} if use_gateway else {}),
                    async_client=client,
                )
                response = await classifier.ainvoke(
                    {"state": task, "questions": dict(questions)},
                    config={"tags": ["nostream"]},
                )
            break
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
                        "classifier_transport": "gateway" if use_gateway else "direct",
                        "status_code": status,
                        "error_type": type(exc).__name__,
                        "gateway_retry_available": bool(not use_gateway and gateway_key),
                    },
                )
                if not use_gateway and gateway_key:
                    continue
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
