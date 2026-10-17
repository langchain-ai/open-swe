import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from math import isfinite
from typing import Literal

import httpx2
from langchain_typesafe import (
    Choice,
    ClassifierResponse,
    Question,
    State,
    TypeSafeClassifier,
)

from openswe.config import ENV
from openswe.utils.gateway import gateway_base_url
from openswe.utils.http_pool import pooled_transport

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


class JevUnavailableError(RuntimeError):
    """Neither a TypeSafe key nor a LangSmith gateway key is configured."""


def _jev_keys() -> tuple[str | None, str | None]:
    typesafe_key = ENV.TYPESAFE_API_KEY.optional()
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()
    if not typesafe_key and not gateway_key:
        raise JevUnavailableError("Jev needs TYPESAFE_API_KEY or a LangSmith gateway key")
    return typesafe_key, gateway_key


def ensure_jev_configured() -> None:
    """Raise `JevUnavailableError` now, before a caller commits to a response."""
    _jev_keys()


@asynccontextmanager
async def jev_classifier(timeout: float) -> AsyncIterator[TypeSafeClassifier]:
    typesafe_key, gateway_key = _jev_keys()
    async with httpx2.AsyncClient(transport=pooled_transport(), timeout=timeout) as client:
        yield TypeSafeClassifier(
            model="jev-1.13.0" if typesafe_key else "typesafe/jev-1.13.0",
            api_key=typesafe_key or gateway_key,
            **({} if typesafe_key else {"base_url": gateway_base_url()}),
            async_client=client,
        )


async def classify_with_jev(
    state: State,
    questions: Mapping[str, Question],
    *,
    timeout: float,
) -> ClassifierResponse:
    async with asyncio.timeout(timeout), jev_classifier(timeout) as classifier:
        return await classifier.ainvoke(
            {"state": state, "questions": dict(questions)}, config={"tags": ["nostream"]}
        )


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
        decision.reason = "classifier_error"
    try:
        response = await classify_with_jev(task, questions, timeout=JEV_TIMEOUT_SECONDS)
    except JevUnavailableError:
        for decision in tracked.values():
            decision.reason = "missing_credentials"
        logger.warning("Jev classification has no API key", extra={"questions": list(questions)})
        return results
    except Exception:
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
