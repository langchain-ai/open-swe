"""The route a thread's model calls take, as the classifier reads the person's request.

The backend's ``classify_route`` (``openswe/middleware/model_selection.py``), moved here
so a turn's route costs no call to the backend: Jev (``runtime_api.classifier``) needs
only the LLM gateway's key, which this deployment calls its models with. The backend
keeps its own for the runs it answers from their plan. The prompts are
``prompts/model-selection``; ``tests/agent/test_mda_routing_prompts.py`` checks them
against the backend's.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from langchain_core.messages import HumanMessage
from runtime_api.catalog import ROUTES
from runtime_api.classifier import JevDecision, select_jev_choice

from rt.input_messages import input_message_text, message_sender_id
from rt.prompts import prompt

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
        decision = JevDecision()
        chosen = await select_jev_choice(
            self.latest_task(messages)[-_TASK_CHARS:],
            question="route",
            instructions=prompt("model-selection/instructions"),
            criteria={route: prompt(f"model-selection/{route}") for route in ROUTES},
            decision=decision,
        )
        if chosen is None or decision.outcome != "accepted":
            return Route("default", persist=False)
        return Route(chosen)
