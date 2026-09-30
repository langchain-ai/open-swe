import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal

import httpx2
from langchain_typesafe import Choice, TypeSafeClassifier

from agent.config import ENV
from agent.utils.gateway import gateway_base_url

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
    decision = decision if decision is not None else JevDecision()
    decision.outcome = "classifier_failure"
    decision.reason = "missing_credentials"
    typesafe_key = ENV.TYPESAFE_API_KEY.optional()
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()
    if not typesafe_key and not gateway_key:
        logger.warning("Jev classification has no API key", extra={"question": question})
        return None
    decision.reason = "classifier_error"
    try:
        async with (
            asyncio.timeout(JEV_TIMEOUT_SECONDS),
            httpx2.AsyncClient(timeout=JEV_TIMEOUT_SECONDS) as client,
        ):
            classifier = TypeSafeClassifier(
                model="jev-1.13.0" if typesafe_key else "typesafe/jev-1.13.0",
                api_key=typesafe_key or gateway_key,
                **({} if typesafe_key else {"base_url": gateway_base_url()}),
                async_client=client,
            )
            response = await classifier.ainvoke(
                {
                    "state": task,
                    "questions": {
                        question: Choice(instructions=instructions, criteria=dict(criteria.items()))
                    },
                },
                config={"tags": ["nostream"]},
            )
            answer = response.choices[question]
        decision.choice = answer.choice if answer.choice in criteria else None
        decision.confidence = answer.confidence if isfinite(answer.confidence) else None
        if not 0.6 <= answer.confidence <= 1.0:
            logger.info(
                "Jev classification confidence below threshold or invalid",
                extra={"question": question, "confidence": answer.confidence},
            )
            decision.outcome = "low_confidence"
            decision.reason = "confidence_below_threshold_or_invalid"
            return None
        for choice in criteria:
            if answer.choice == choice:
                decision.outcome = "accepted"
                decision.reason = "confident_choice"
                return choice
        decision.reason = "invalid_choice"
        raise ValueError("Jev returned a choice outside the supplied criteria")
    except Exception:
        logger.exception("Jev classification failed", extra={"question": question})
        return None
