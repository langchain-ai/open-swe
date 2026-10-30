"""Linear automations: what an issue event starts, decided in this deployment.

An automation fires on a Linear team's issue events: an issue created, or one that
gained a label. Its trigger may also ask for labels and a project. This side reads
the event, asks the backend for the team's automations
(``linear_automation_rules``), and matches them. For each match the backend claims
the automation once per delivery, within its hourly cap, writes the run's prompt
and signs its context (``plan_linear_automation``); the channel starts the run on a
thread of its own, so automations never touch the issue's conversation.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from managed_deepagents import (
    ChannelRunInput,
    ChannelRunOptions,
    LinearChannelThread,
    LinearEventResult,
    LinearIssueCreateEvent,
    LinearIssueUpdateEvent,
)
from pydantic import JsonValue
from runtime_api.linear import (
    LINEAR_AUTOMATION_RULES,
    PLAN_LINEAR_AUTOMATION,
    TEAM_KEY_PATTERN,
    LinearAutomation,
    LinearAutomationRulesRequest,
    LinearTrigger,
    LinearTriggerEvent,
    PlanLinearAutomation,
)

from rt.intake import ask_backend
from rt.linear import record

logger = logging.getLogger(__name__)

_TEAM_KEY: Final = re.compile(TEAM_KEY_PATTERN)


def _names(items: object) -> dict[str, str]:
    """``{id: name}`` for a Linear list of ``{id, name}`` objects."""
    if not isinstance(items, list):
        return {}
    return {
        str(item["id"]): str(item.get("name") or "")
        for item in cast(list[object], items)
        if isinstance(item, Mapping) and item.get("id")
    }


def _team(data: Mapping[str, object]) -> str:
    team = data.get("team")
    key = team.get("key") if isinstance(team, Mapping) else None
    if isinstance(key, str) and key:
        return key.upper()
    identifier = data.get("identifier")
    if isinstance(identifier, str) and "-" in identifier:
        return identifier.rsplit("-", 1)[0].upper()
    return ""


@dataclass(frozen=True)
class IssueEvent:
    """What a Linear issue delivery stands for, as automations' triggers read it."""

    events: frozenset[LinearTriggerEvent]
    team: str
    labels: frozenset[str]
    # The label names an update added.
    added_labels: frozenset[str]
    project: str
    data: dict[str, JsonValue]

    @classmethod
    def of(cls, payload: Mapping[str, object]) -> IssueEvent | None:
        """The trigger events a delivery stands for; ``None`` when it stands for none."""
        data = payload.get("data")
        if payload.get("type") != "Issue" or not isinstance(data, Mapping):
            return None
        issue = cast(Mapping[str, object], data)
        names = _names(issue.get("labels"))
        events: frozenset[LinearTriggerEvent] = frozenset()
        added: frozenset[str] = frozenset()
        if payload.get("action") == "create":
            events = frozenset({"issue.created"})
        elif payload.get("action") == "update":
            before = cast(Mapping[str, object], payload.get("updatedFrom") or {}).get("labelIds")
            after = issue.get("labelIds")
            if isinstance(before, list) and isinstance(after, list):
                added_ids = {str(label) for label in after} - {str(label) for label in before}
                added = frozenset(names.get(label_id, "") for label_id in added_ids) - {""}
                events = frozenset({"issue.labeled"}) if added_ids else frozenset()
        team = _team(issue)
        if not events or not _TEAM_KEY.fullmatch(team):
            return None
        project = issue.get("project")
        project_name = project.get("name") if isinstance(project, Mapping) else None
        return cls(
            events=events,
            team=team,
            labels=frozenset(names.values()) - {""},
            added_labels=added,
            project=str(project_name or ""),
            data=cast(dict[str, JsonValue], dict(issue)),
        )

    def fires(self, trigger: LinearTrigger, event: LinearTriggerEvent) -> bool:
        if event not in self.events or event not in trigger.events:
            return False
        if trigger.project and trigger.project.lower() != self.project.lower():
            return False
        wanted = {label.lower() for label in trigger.labels}
        if not wanted:
            return True
        candidates = self.added_labels if event == "issue.labeled" else self.labels
        return bool(wanted & {label.lower() for label in candidates})

    def firing(self, automation: LinearAutomation) -> LinearTriggerEvent | None:
        """The event an automation fires on for this delivery, if any; once per delivery."""
        return next(
            (
                event
                for trigger in automation.triggers
                for event in sorted(self.events)
                if self.fires(trigger, event)
            ),
            None,
        )


async def start_automations(
    event: LinearIssueCreateEvent | LinearIssueUpdateEvent, thread: LinearChannelThread
) -> LinearEventResult:
    """Log the delivery, and start a run for each automation it fires."""
    await record(event, thread)
    issue = IssueEvent.of(cast(Mapping[str, object], event.payload))
    if issue is None:
        return None
    automations = (
        await ask_backend(
            LINEAR_AUTOMATION_RULES,
            LinearAutomationRulesRequest(team=issue.team),
            msg_id=f"msg_{event.delivery_id}_automations",
        )
    ).automations
    runs: list[tuple[ChannelRunInput, ChannelRunOptions]] = []
    for automation in automations:
        fired = issue.firing(automation)
        if fired is None:
            continue
        try:
            planned = (
                await ask_backend(
                    PLAN_LINEAR_AUTOMATION,
                    PlanLinearAutomation(
                        automation_id=automation.id,
                        delivery_id=event.delivery_id,
                        event=fired,
                        team=issue.team,
                        issue=issue.data,
                        labels=sorted(issue.labels),
                        project=issue.project,
                    ),
                    msg_id=f"msg_{event.delivery_id}_automation_{automation.id}",
                )
            ).run
        except Exception:
            # One automation that can't start doesn't keep the others from starting.
            logger.exception(
                "A Linear automation could not be planned",
                extra={"automation_id": automation.id, "linear_delivery": event.delivery_id},
            )
            continue
        if planned is None:
            continue
        options: ChannelRunOptions = {
            "thread": {"mode": "persistent", "id": str(planned.thread_id)},
            "context": planned.context,
            "metadata": planned.metadata,
        }
        if planned.webhook:
            options["webhook"] = planned.webhook
        runs.append((planned.input, options))
    return runs or None
