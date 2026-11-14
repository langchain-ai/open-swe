"""Jev, the LangSmith LLM gateway's classifier: typed answers to questions about a task.

Both sides ask it: the backend to read a model request and pick reviewers, the runtime
to route a thread (``mda/rt/routing.py``). A key from the environment authenticates it,
as the run's models are (``runtime_api.models``); without one, or when it fails or is
unsure, it chooses nothing and ``JevDecision`` says why.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal

import httpx2
from langchain_typesafe import Choice, ClassifierResponse, Question, State, TypeSafeClassifier
from langchain_typesafe.client import TypeSafePermissionDeniedError

from runtime_api.models import (
    GATEWAY_MODEL_ROUTE_HEADER,
    GATEWAY_SERVICE_HEADER,
    GATEWAY_SERVICE_NAME,
    gateway_api_key,
    gateway_base_url,
)

logger = logging.getLogger(__name__)

JEV_TIMEOUT_SECONDS = 3.0
# Pinned so confidence thresholds tuned against it keep holding.
SYSTEM_ONE_MODEL = "typesafe/jev-1.13.0"


class JevUnavailable(Exception):
    """Jev gave no answer: no key, a rejected key, or a failed call."""

    def __init__(
        self, reason: Literal["missing_credentials", "classifier_auth_failure", "classifier_error"]
    ) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class JevDecision:
    choice: str | None = None
    confidence: float | None = None
    outcome: Literal["not_run", "accepted", "low_confidence", "classifier_failure"] = "not_run"
    reason: str = "not_run"


@dataclass(frozen=True)
class Jev:
    """TypeSafe's Jev, reached through the LangSmith LLM Gateway.

    The gateway key is read from the environment on every call, so one client serves a
    whole process; ``transport`` builds each call's HTTP transport.
    """

    transport: Callable[[], httpx2.AsyncBaseTransport] | None = None
    timeout: float | None = None

    @staticmethod
    def configured() -> bool:
        """Whether this deployment has a gateway key to reach Jev with."""
        return bool(gateway_api_key())

    async def ask(self, task: State, *, questions: Mapping[str, Question]) -> ClassifierResponse:
        """Jev's answers to ``questions`` about ``task``; raises ``JevUnavailable`` without one."""
        api_key = gateway_api_key()
        if not api_key:
            logger.warning(
                "Jev classification has no API key", extra={"questions": list(questions)}
            )
            raise JevUnavailable("missing_credentials")
        timeout = self.timeout or JEV_TIMEOUT_SECONDS
        try:
            async with (
                asyncio.timeout(timeout),
                httpx2.AsyncClient(
                    transport=self.transport() if self.transport else None,
                    timeout=timeout,
                    headers={
                        GATEWAY_SERVICE_HEADER: GATEWAY_SERVICE_NAME,
                        GATEWAY_MODEL_ROUTE_HEADER: "default",
                    },
                ) as client,
            ):
                classifier = TypeSafeClassifier(
                    model=SYSTEM_ONE_MODEL,
                    api_key=api_key,
                    base_url=gateway_base_url(),
                    async_client=client,
                )
                return await classifier.ainvoke(
                    {"state": task, "questions": dict(questions)},
                    config={"tags": ["nostream"]},
                )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is None:
                status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(exc, TypeSafePermissionDeniedError) or status in (401, 403):
                logger.error(
                    "Jev classifier credential rejected",
                    extra={
                        "questions": list(questions),
                        "status_code": status,
                        "error_type": type(exc).__name__,
                    },
                )
                raise JevUnavailable("classifier_auth_failure") from exc
            logger.exception("Jev classification failed", extra={"questions": list(questions)})
            raise JevUnavailable("classifier_error") from exc

    async def choose(
        self,
        task: State,
        *,
        questions: Mapping[str, Choice],
        decisions: Mapping[str, JevDecision | None] | None = None,
        min_confidence: float = 0.6,
    ) -> dict[str, str | None]:
        """Each question's confident choice, or None; ``decisions`` record why."""
        results: dict[str, str | None] = dict.fromkeys(questions)
        tracked = {q: (decisions or {}).get(q) or JevDecision() for q in questions}
        for decision in tracked.values():
            decision.outcome = "classifier_failure"
            decision.reason = "classifier_error"
        try:
            response = await self.ask(task, questions=questions)
        except JevUnavailable as exc:
            for decision in tracked.values():
                decision.reason = exc.reason
            return results
        for question, spec in questions.items():
            decision = tracked[question]
            try:
                answer = response.choices[question]
                decision.choice = answer.choice if answer.choice in spec.criteria else None
                decision.confidence = answer.confidence if isfinite(answer.confidence) else None
                if not min_confidence <= answer.confidence <= 1.0:
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

    async def choose_one[ChoiceT: str](
        self,
        task: State,
        *,
        question: str,
        instructions: str,
        criteria: Mapping[ChoiceT, str],
        decision: JevDecision | None = None,
        min_confidence: float = 0.6,
    ) -> ChoiceT | None:
        """The confident choice for one question, or None."""
        choices = await self.choose(
            task,
            questions={
                question: Choice(instructions=instructions, criteria=dict(criteria.items()))
            },
            decisions={question: decision},
            min_confidence=min_confidence,
        )
        return next((choice for choice in criteria if choice == choices[question]), None)


# Jev over a fresh transport per call, for callers without a connection pool of their own.
DEFAULT_JEV = Jev()
