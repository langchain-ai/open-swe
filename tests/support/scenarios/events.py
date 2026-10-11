"""The event log as a scenario boundary: each decision Open SWE emits becomes a moment."""

from collections.abc import Callable

from pydantic import JsonValue

from openswe.webhooks.event_log import EventLog, EventRefs
from tests.support.scenarios.core import Fake, Scenario

type Describe = Callable[[str, dict[str, JsonValue]], str]


class Decisions:
    """``EventLog.emit`` calls, named by ``describe`` for expectations and the page."""

    def __init__(self, scenario: Scenario, describe: Describe) -> None:
        self.scenario = scenario
        self.describe = describe

    async def _emit(self, event_type: str, payload: dict[str, JsonValue], refs: EventRefs) -> None:
        self.scenario.record("decision", self.describe(event_type, payload), data=payload)

    def fakes(self) -> list[tuple[object, Fake]]:
        return []

    def attribute_fakes(self) -> list[tuple[object, str, object]]:
        return [(EventLog, "emit", self._emit)]
