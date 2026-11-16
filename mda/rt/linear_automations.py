"""Linear automations: what an issue event starts, decided in this deployment.

An automation fires on a Linear team's issue events: an issue created, or one that
gained a label. Its trigger may also ask for labels and a project. This side reads
the event, asks the backend for the team's automations
(``linear_automation_rules``, which also logs the delivery), matches them, and
writes each match's prompt: the automation's own, then the issue, fenced as
untrusted unless its creator is an Open SWE user. For all the matches at once the
backend claims each automation once per delivery, within its hourly cap, and signs
its run's context (``plan_linear_automations``); the channel starts each run on a
thread of its own, so automations never touch the issue's conversation.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, cast

import httpx2
from managed_deepagents import (
    ChannelRun,
    LinearChannelThread,
    LinearEventResult,
    LinearIssueCreateEvent,
    LinearIssueUpdateEvent,
    connections,
)
from pydantic import JsonValue
from runtime_api.connections import LINEAR_APP_TOKEN
from runtime_api.linear import (
    LINEAR_AUTOMATION_RULES,
    PLAN_LINEAR_AUTOMATIONS,
    TEAM_KEY_PATTERN,
    FiredLinearAutomation,
    LinearAutomation,
    LinearAutomationRulesRequest,
    LinearTrigger,
    LinearTriggerEvent,
    PlanLinearAutomations,
)

from rt.intake import ask_backend
from rt.linear import delivery_of, record
from rt.prompts import prompt
from rt.untrusted import fence

logger = logging.getLogger(__name__)

_TEAM_KEY: Final = re.compile(TEAM_KEY_PATTERN)
_EVENT_DESCRIPTIONS: Final[dict[LinearTriggerEvent, str]] = {
    "issue.created": "an issue was created",
    "issue.labeled": "a label was added to an issue",
    "issue.accepted": "an issue was accepted out of Triage",
}
_DESCRIPTION_MAX_CHARS: Final = 8_000


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
            updated_from = payload.get("updatedFrom")
            if not isinstance(updated_from, Mapping):
                return None
            before = updated_from.get("labelIds")
            after = issue.get("labelIds")
            state = issue.get("state")
            if cls.first_triage(issue, updated_from) and (
                not isinstance(state, Mapping)
                or not isinstance(state.get("type"), str)
                or state.get("type") in {"backlog", "unstarted", "started", "completed"}
            ):
                events = frozenset({"issue.accepted"})
            if isinstance(before, list) and isinstance(after, list):
                added_ids = {str(label) for label in after} - {str(label) for label in before}
                added = frozenset(names.get(label_id, "") for label_id in added_ids) - {""}
                if added_ids:
                    events = events | frozenset[LinearTriggerEvent]({"issue.labeled"})
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

    @staticmethod
    def first_triage(data: Mapping[str, object], before: Mapping[str, object]) -> bool:
        return (
            "triagedAt" in before
            and before["triagedAt"] is None
            and isinstance(data.get("triagedAt"), str)
            and bool(data["triagedAt"])
        )

    async def resolve_state(self) -> IssueEvent:
        """Resolve an acceptance candidate's destination with the deployment's Linear app."""
        state = self.data.get("state")
        if "issue.accepted" not in self.events or (
            isinstance(state, dict) and isinstance(state.get("type"), str)
        ):
            return self
        state_id = self.data.get("stateId")
        if not isinstance(state_id, str) or not state_id:
            raise ValueError("Linear acceptance has no destination state ID")
        token = await connections.get(LINEAR_APP_TOKEN, {"type": "agent"})
        async with httpx2.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                "https://api.linear.app/graphql",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "query": (
                        "query DestinationState($id: String!) { workflowState(id: $id) "
                        "{ id name type team { key } } }"
                    ),
                    "variables": {"id": state_id},
                },
            )
        if response.status_code != 200:
            raise RuntimeError("Linear destination state request failed")
        body: object = response.json()
        result = body.get("data") if isinstance(body, dict) and not body.get("errors") else None
        resolved = result.get("workflowState") if isinstance(result, dict) else None
        team = resolved.get("team") if isinstance(resolved, dict) else None
        if (
            not isinstance(resolved, dict)
            or resolved.get("id") != state_id
            or not isinstance(resolved.get("type"), str)
            or not isinstance(team, dict)
            or team.get("key") != self.team
        ):
            raise ValueError("Linear destination state response does not match the issue")
        events = self.events
        if resolved["type"] not in {"backlog", "unstarted", "started", "completed"}:
            events = events - {"issue.accepted"}
        return IssueEvent(
            events=events,
            team=self.team,
            labels=self.labels,
            added_labels=self.added_labels,
            project=self.project,
            data={
                **self.data,
                "state": {"type": resolved["type"], "name": str(resolved.get("name") or "")},
            },
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

    def context(self, *, registered: bool) -> str:
        """The issue, as a run's prompt quotes it: fenced unless its creator is registered."""
        data = self.data
        creator = data.get("creator")
        creator_name = creator.get("name") if isinstance(creator, dict) else None
        state = data.get("state")
        state_name = state.get("name") if isinstance(state, dict) else None
        lines = [
            f"Issue: {data.get('identifier') or ''} {data.get('title') or ''}",
            f"URL: {data.get('url') or ''}",
            f"Team: {self.team}",
            f"Project: {self.project or '(none)'}",
            f"Labels: {', '.join(sorted(self.labels)) or '(none)'}",
            f"State: {state_name or ''}",
            f"Creator: {creator_name or ''}",
        ]
        description = str(data.get("description") or "")[:_DESCRIPTION_MAX_CHARS]
        return fence("\n".join(lines) + f"\n\n{description}", registered=registered)

    def prompt(
        self, automation: LinearAutomation, event: LinearTriggerEvent, *, registered: bool
    ) -> str:
        """The run's prompt: the automation's own, then the event and the issue."""
        return prompt(
            "linear-automation-event",
            prompt=automation.prompt,
            event_description=_EVENT_DESCRIPTIONS[event],
            event_context=self.context(registered=registered),
        )


async def start_automations(
    event: LinearIssueCreateEvent | LinearIssueUpdateEvent, thread: LinearChannelThread
) -> LinearEventResult:
    """Log the delivery, and start a run for each automation it fires."""
    issue = IssueEvent.of(cast(Mapping[str, object], event.payload))
    if issue is None:
        return await record(event, thread)
    rules = await ask_backend(
        LINEAR_AUTOMATION_RULES,
        LinearAutomationRulesRequest(delivery=delivery_of(event), team=issue.team),
        msg_id=f"msg_{event.delivery_id}_automations",
    )
    if any(
        issue.fires(trigger, "issue.accepted")
        for automation in rules.automations
        for trigger in automation.triggers
    ):
        try:
            issue = await issue.resolve_state()
        except Exception:
            # Linear won't resend the delivery, so a failed lookup drops only the
            # acceptance: the delivery's other events still start their automations.
            logger.warning("Could not resolve a Linear acceptance's destination", exc_info=True)
            issue = replace(issue, events=issue.events - {"issue.accepted"})
    fired = [
        FiredLinearAutomation(
            automation_id=automation.id,
            event=fires_on,
            prompt=issue.prompt(automation, fires_on, registered=rules.creator_registered),
        )
        for automation in rules.automations
        if (fires_on := issue.firing(automation)) is not None
    ]
    if not fired:
        return None
    # Linear has its answer already and won't send the delivery again, so the call is
    # tried again for as long as a backend rollout takes: automations already claimed
    # for the delivery plan the same threads, which start their runs once.
    planned_runs = (
        await ask_backend(
            PLAN_LINEAR_AUTOMATIONS,
            PlanLinearAutomations(
                delivery_id=event.delivery_id,
                automations=fired,
                team=issue.team,
            ),
            msg_id=f"msg_{event.delivery_id}_automations_plan",
        )
    ).runs
    runs: list[ChannelRun] = []
    for planned in planned_runs:
        if planned is None:
            continue
        run: ChannelRun = {
            "input": planned.input,
            "thread": {"mode": "persistent", "id": str(planned.thread_id)},
            "context": planned.context,
            "metadata": planned.metadata,
        }
        if planned.webhook:
            run["webhook"] = planned.webhook
        runs.append(run)
    return runs or None
