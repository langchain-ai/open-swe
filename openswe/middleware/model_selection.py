from typing import Annotated, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, OmitFromOutput
from runtime_api.catalog import RouteName

SelectedRoute = RouteName
PersistedRoute = SelectedRoute | Literal["fast_alt"]
RoutingMode = Literal["auto", "fast"]


class ModelSelectionState(AgentState):
    model_route: NotRequired[PersistedRoute]
    requested_model: NotRequired[Annotated[str | None, OmitFromOutput]]
    requested_effort: NotRequired[Annotated[str | None, OmitFromOutput]]
