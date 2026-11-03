"""The route a thread's model calls take, as the classifier reads the person's request.

The backend's ``classify_route`` (``openswe/middleware/model_selection.py``) and
``select_jev_choice`` (``openswe/utils/jev.py``), moved here so a turn's route costs
no call to the backend: the classifier needs only the LLM gateway's key, which this
deployment calls its models with. The backend keeps its own for the runs it still
answers from their plan. The prompts are ``prompts/model-selection``;
``tests/agent/test_mda_routing_prompts.py`` checks them against the backend's.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import httpx2
from langchain_core.messages import HumanMessage
from langchain_typesafe import Choice, TypeSafeClassifier
from langchain_typesafe.client import TypeSafePermissionDeniedError

from rt.input_messages import input_message_text, message_sender_id
from rt.models import gateway_api_key, gateway_base_url
from rt.prompts import prompt

logger = logging.getLogger(__name__)

ROUTES: Final = ("fast", "balanced", "performance")
_QUESTION: Final = "route"
_MODEL: Final = "typesafe/jev-1.13.0"
_TIMEOUT_SECONDS: Final = 3.0
_MIN_CONFIDENCE: Final = 0.6
# The classifier reads the end of a long request.
_TASK_CHARS: Final = 8_000


@dataclass(frozen=True)
class Route:
    # One of ``ROUTES``, or ``default``.
    route: str
    # False when the classifier could not decide; the run uses the route for this run
    # only, so a later turn classifies again instead of keeping a fallback.
    persist: bool = True


class RouteClassifier:
    """Picks the least expensive route likely to complete the person's latest request."""

    @staticmethod
    def latest_task(messages: Sequence[object]) -> str:
        """The person's own request, skipping injected context envelopes.

        Context blocks (sender metadata, dynamic context) are appended as
        ``HumanMessage``s after the real input, so the newest ``HumanMessage`` is
        usually machine-authored. Only ``kind="human"`` envelopes carry a request.
        """
        plain = ""
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            content = message.content
            if message_sender_id(content, kind="human") is not None:
                if authored := input_message_text(content):
                    return authored
                continue
            text = message.text
            if plain or not isinstance(text, str) or "<dynamic-context" in text:
                continue
            if "<input-message" not in text:
                plain = text
        return plain

    async def classify(self, messages: Sequence[object]) -> Route:
        """The route the person's latest request takes; ``default`` for this run alone when
        the classifier cannot say."""
        chosen = await self._choose(self.latest_task(messages)[-_TASK_CHARS:])
        return Route(chosen) if chosen is not None else Route("default", persist=False)

    async def _choose(self, task: str) -> str | None:
        api_key = gateway_api_key()
        if not api_key:
            logger.warning("Route classification has no API key")
            return None
        question = Choice(
            instructions=prompt("model-selection/instructions"),
            criteria={route: prompt(f"model-selection/{route}") for route in ROUTES},
        )
        try:
            async with (
                asyncio.timeout(_TIMEOUT_SECONDS),
                httpx2.AsyncClient(timeout=_TIMEOUT_SECONDS) as client,
            ):
                classifier = TypeSafeClassifier(
                    model=_MODEL,
                    api_key=api_key,
                    base_url=gateway_base_url(),
                    async_client=client,
                )
                response = await classifier.ainvoke(
                    {"state": task, "questions": {_QUESTION: question}},
                    # The classifier's tokens are not the run's to stream.
                    config={"tags": ["nostream"]},
                )
            answer = response.choices[_QUESTION]
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is None:
                status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(exc, TypeSafePermissionDeniedError) or status in (401, 403):
                logger.error(
                    "Route classifier credential rejected",
                    extra={"status_code": status, "error_type": type(exc).__name__},
                )
            else:
                logger.exception("Route classification failed")
            return None
        confidence = answer.confidence
        if not _MIN_CONFIDENCE <= confidence <= 1.0:
            logger.info(
                "Route classification confidence below threshold or invalid",
                extra={"confidence": confidence},
            )
            return None
        if answer.choice not in ROUTES:
            logger.error("Route classifier chose a route outside the criteria")
            return None
        return answer.choice
