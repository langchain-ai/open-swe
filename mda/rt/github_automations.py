"""GitHub automations: what an issue or pull request event starts, decided in this deployment.

An automation fires on a repository's events: an issue opened, a pull request
opened, closed or merged. This side reads the delivery, asks the backend for the
repository's automations (``github_automation_rules``), matches them, and writes
each match's prompt: the automation's own, then the issue or pull request, fenced
as untrusted unless its author is an Open SWE user. The backend claims each once
per delivery, checks the public-repo gate and signs its run
(``act_on_github_delivery``); the channel starts each run on a thread of its own,
so automations never touch the issue's conversation. It writes the prompt byte for
byte as the backend's ``_github_event_prompt`` did:
``tests/github_prompt_backend.json`` holds what it wrote for each case.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from runtime_api.github import (
    FiredGitHubAutomation,
    GitHubAutomation,
    GitHubAutomationRules,
    GitHubTriggerEvent,
    PullRequestBranches,
)

from rt.prompts import prompt
from rt.untrusted import fence

_EVENT_DESCRIPTIONS: Final[dict[GitHubTriggerEvent, str]] = {
    "issues.opened": "an issue was opened",
    "pull_request.opened": "a pull request was opened",
    "pull_request.closed": "a pull request was closed",
    "pull_request.merged": "a pull request was merged",
}


def _fields(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class SubjectEvent:
    """An issue or pull request event, as automations' triggers read it."""

    event_type: str
    events: frozenset[GitHubTriggerEvent]
    # The issue or pull request.
    subject: Mapping[str, object]

    @classmethod
    def of(cls, event_type: str, payload: Mapping[str, object]) -> SubjectEvent | None:
        """The trigger events a delivery stands for; ``None`` when it stands for none."""
        action = payload.get("action")
        events: frozenset[GitHubTriggerEvent] = frozenset()
        if event_type == "issues" and action == "opened":
            events = frozenset({"issues.opened"})
        elif event_type == "pull_request" and action == "opened":
            events = frozenset({"pull_request.opened"})
        elif event_type == "pull_request" and action == "closed":
            merged = _fields(payload.get("pull_request")).get("merged") is True
            events = frozenset(
                {"pull_request.closed", "pull_request.merged"}
                if merged
                else {"pull_request.closed"}
            )
        if not events:
            return None
        subject = payload.get("issue" if event_type == "issues" else "pull_request")
        return cls(event_type=event_type, events=events, subject=_fields(subject))

    @property
    def author(self) -> str:
        """Who opened the issue or pull request."""
        return str(_fields(self.subject.get("user")).get("login") or "")

    @property
    def branches(self) -> PullRequestBranches | None:
        """The pull request's branches, which a trigger may filter on; ``None`` for an issue."""
        return None if self.event_type == "issues" else PullRequestBranches.of(self.subject)

    def firing(self, automation: GitHubAutomation) -> GitHubTriggerEvent | None:
        """The event an automation fires on, if any: a merge over a plain close."""
        fired = sorted(event for event in self.events if event in automation.events)
        if not fired:
            return None
        return "pull_request.merged" if "pull_request.merged" in fired else fired[0]

    def context(self, *, registered: bool) -> str:
        """The issue or pull request, as a run's prompt quotes it."""
        subject = self.subject
        issue = self.event_type == "issues"
        lines = [
            f"{'Issue' if issue else 'Pull request'}: "
            f"#{subject.get('number', '')} {subject.get('title', '')}",
            f"URL: {subject.get('html_url', '')}",
            f"Author: {self.author}",
        ]
        if not issue:
            base, head = _fields(subject.get("base")), _fields(subject.get("head"))
            lines += [
                f"Base: {base.get('ref', '')}  Head: {head.get('ref', '')}",
                f"Merged: {'yes' if subject.get('merged') is True else 'no'}",
            ]
        context = "\n".join(lines) + f"\n\n{subject.get('body') or ''}"
        return fence(context, registered=registered)

    def prompt(
        self, automation: GitHubAutomation, event: GitHubTriggerEvent, *, registered: bool
    ) -> str:
        """The run's prompt: the automation's own, then the event and its subject."""
        return prompt(
            "github-automation-event",
            prompt=automation.prompt,
            event_description=_EVENT_DESCRIPTIONS[event],
            event_context=self.context(registered=registered),
        )

    def fired(self, rules: GitHubAutomationRules) -> list[FiredGitHubAutomation]:
        """Each automation the event fires, with the prompt of its run."""
        return [
            FiredGitHubAutomation(
                automation_id=automation.id,
                event=event,
                prompt=self.prompt(automation, event, registered=rules.author_registered),
            )
            for automation in rules.automations
            if (event := self.firing(automation)) is not None
        ]
