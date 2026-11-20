from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, OmitFromOutput
from langchain_core.messages import HumanMessage
from runtime_api.catalog import RouteName
from runtime_api.provenance import Provenance
from runtime_api.route_questions import TaskRoute

from openswe.input_messages import input_message_text
from openswe.prompts import prompt
from openswe.utils.jev import JEV

SelectedRoute = RouteName
PersistedRoute = SelectedRoute | Literal["fast_alt"]
RoutingMode = Literal["auto", "fast"]


def _latest_task(messages: Sequence[object]) -> str:
    """What a person last wrote, skipping the context around it; for a run no person
    started (a schedule, a background event), the latest event sent to it."""
    event = ""
    for message in reversed(messages):
        if not isinstance(message, HumanMessage) or (provenance := Provenance.of(message)) is None:
            continue
        if provenance.human:
            return input_message_text(message.content) or ""
        if not event and provenance.kind == "system":
            event = input_message_text(message.content) or ""
    return event


@dataclass(frozen=True)
class RouteSelection:
    route: SelectedRoute
    # False when the classifier was unsure: the route holds for this run alone.
    persist: bool = True


async def _select_jev_route(task: str) -> RouteSelection:
    route, persist = await TaskRoute.classify(task, prompt=prompt, jev=JEV)
    return RouteSelection(route or "default", persist)


async def classify_route(messages: Sequence[object]) -> RouteSelection:
    """The route the latest request takes, as the classifier reads it."""
    return await _select_jev_route(_latest_task(messages))


class ModelSelectionState(AgentState):
    model_route: NotRequired[PersistedRoute]
    requested_model: NotRequired[Annotated[str | None, OmitFromOutput]]
    requested_effort: NotRequired[Annotated[str | None, OmitFromOutput]]
