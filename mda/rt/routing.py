"""The route a thread's model calls take, as the classifier reads the person's request.

The backend's ``classify_route`` (``openswe/middleware/model_selection.py``), moved here
so a turn's route costs no call to the backend: Jev (``runtime_api.classifier``) needs
only the LLM gateway's key, which this deployment calls its models with. The backend
keeps its own for the runs it answers from their plan. Both ask the questions in
``runtime_api.route_questions``, worded by ``prompts/model-selection``, which
``tests/agent/test_mda_routing_prompts.py`` checks against the backend's.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import HumanMessage
from runtime_api.provenance import Provenance
from runtime_api.route_questions import TaskRoute

from rt.input_messages import input_message_text
from rt.prompts import prompt


@dataclass(frozen=True)
class Route:
    # One of ``ROUTES``, or ``default``.
    route: str
    # False when the classifier could not decide; the run uses the route for this run
    # only, so a later turn classifies again instead of keeping a fallback.
    persist: bool = True


class RouteClassifier:
    """Routes the person's latest request by what kind of task it is."""

    @staticmethod
    def latest_task(messages: Sequence[object]) -> str:
        """The person's own request, skipping injected context envelopes.

        Context blocks (sender metadata, dynamic context) are appended as
        ``HumanMessage``s after the real input, so the newest ``HumanMessage`` is
        usually machine-authored. Only a message whose provenance says a person wrote it
        carries a request.
        """
        plain = ""
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            content = message.content
            if (provenance := Provenance.of(message)) is not None and provenance.human:
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
        chosen, persist = await TaskRoute.classify(self.latest_task(messages), prompt=prompt)
        if chosen is None:
            return Route("default", persist=False)
        return Route(chosen, persist=persist)
