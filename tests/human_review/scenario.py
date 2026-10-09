"""Reviewer assignment as a scenario: people who review each other's pull requests.

A ``ReviewScenario`` adds code owners, habits, one pull request, and the actions people take to
the general scenario core. Everything goes in through Open SWE's public doors: the review is
opened with ``request_review``, clicks are signed Slack payloads posted to the interactivity
webhook, and approvals settle the pull request as a GitHub webhook would. The agent woken to
pick a reviewer reads the real wake-up prompt and answers with the real
``assign_human_reviewer`` tool; by default it takes whatever Open SWE suggests.
"""

import re
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Protocol
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import JsonValue

from openswe import dispatch
from openswe.dashboard import workspace_settings
from openswe.human_review import standard
from openswe.human_review.events import ReviewDecisionCause
from openswe.human_review.notices import NoticeKind
from openswe.human_review.picking import REVIEWER_INSTRUCTIONS_PATH, Coverage
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import parse_github_pr_url
from openswe.tools.request_human_review import assign_human_reviewer
from openswe.workspaces import routing
from tests.support.scenarios.core import (
    AGENT,
    Boundary,
    Fake,
    Person,
    Scenario,
    Seen,
    instance_method,
    invariant,
    returning,
)
from tests.support.scenarios.events import Decisions
from tests.support.scenarios.github import GITHUB_REVIEW_REQUEST, GitHub, ReviewState
from tests.support.scenarios.slack import Directory, Slack

NEW_YORK = "America/New_York"
SAN_FRANCISCO = "America/Los_Angeles"
LONDON = "Europe/London"
BANGALORE = "Asia/Kolkata"
TOKYO = "Asia/Tokyo"

_REVIEWS = "CREVIEWS"
_SIGN_UP = "open_swe_option_select_review"
_PICK_NOTICES = frozenset({"reviewer_pick", "review_snooze_ended"})
_INSTRUCTIONS = re.compile(r"<reviewer-instructions>(.*?)</reviewer-instructions>", re.DOTALL)
_SUGGESTION = re.compile(r"Open SWE suggests @([A-Za-z0-9-]+): (.+?)(?: Unless|$)", re.MULTILINE)

type Habit = Literal["ignore", "accept", "decline", "approve", "accept_then_approve"]
type Button = Literal["ill_review", "accept", "decline", "snooze"]


# What a step can cause.


def picked(person: Person) -> Seen:
    return Seen.decision("reviewer_picked", person)


def joined(person: Person, cause: ReviewDecisionCause) -> Seen:
    return Seen.decision("reviewer_joined", person, cause=cause)


def released(*who: Person, cause: ReviewDecisionCause) -> Seen:
    return Seen.decision("reviewers_released", *who, cause=cause)


def overdue(*who: Person, cause: ReviewDecisionCause) -> Seen:
    return Seen.decision("review_overdue", *who, cause=cause)


def closed(cause: ReviewDecisionCause) -> Seen:
    return Seen.decision("request_closed", cause=cause)


def dm_to(person: Person, notice: NoticeKind) -> Seen:
    return Seen.message(person, notice)


def requested_on_github(person: Person) -> Seen:
    return Seen.message(person, GITHUB_REVIEW_REQUEST)


def edited(person: Person) -> Seen:
    return Seen.edit(person)


# The agent woken to pick a reviewer.


class Agent(Protocol):
    """The agent that owns the review's Slack thread, woken with Open SWE's prompt."""

    async def woken(self, scenario: ReviewScenario, thread_id: str, prompt: str) -> None: ...


@dataclass(frozen=True)
class TakesSuggestions:
    """Assigns every reviewer the prompt suggests, with the tool the prompt names.

    With no suggestion, it assigns ``otherwise`` if given, as its own research of CODEOWNERS
    and history would; without one it picks nobody.
    """

    otherwise: Person | None = None

    async def woken(self, scenario: ReviewScenario, thread_id: str, prompt: str) -> None:
        suggestions = [(login, reason.strip()) for login, reason in _SUGGESTION.findall(prompt)]
        if not suggestions and self.otherwise is not None:
            suggestions = [(self.otherwise.login, "They know this code from its history.")]
        if not suggestions:
            scenario.record("agent", "woken; no suggestion and nobody to pick", source=AGENT)
        for login, reason in suggestions:
            result = await scenario.use_tool(
                thread_id,
                assign_human_reviewer,
                pr_url=scenario.pr_url,
                github_login=login,
                reason=reason,
            )
            outcome = str(result.get("next") if result.get("success") else result.get("error"))
            scenario.record("agent", f"assign_human_reviewer @{login} → {outcome}", source=AGENT)


@dataclass(frozen=True)
class FollowsInstructions:
    """Assigns the first person the repository's reviewer instructions name, as a model would
    read them; without instructions it takes Open SWE's suggestions."""

    async def woken(self, scenario: ReviewScenario, thread_id: str, prompt: str) -> None:
        instructions = _INSTRUCTIONS.search(prompt)
        named = re.search(r"@([A-Za-z0-9-]+)", instructions.group(1)) if instructions else None
        if named is None:
            await TakesSuggestions().woken(scenario, thread_id, prompt)
            return
        result = await scenario.use_tool(
            thread_id,
            assign_human_reviewer,
            pr_url=scenario.pr_url,
            github_login=named.group(1),
            reason="The repository's reviewer instructions name them.",
        )
        outcome = str(result.get("next") if result.get("success") else result.get("error"))
        scenario.record(
            "agent", f"assign_human_reviewer @{named.group(1)} → {outcome}", source=AGENT
        )


@dataclass(frozen=True)
class _HabitRule:
    action: Habit
    after: timedelta


@dataclass(frozen=True)
class _Workspace:
    slug: str


@dataclass(frozen=True)
class _WorkspaceSettings:
    human_review_auto_assign_minutes: int


class ReviewScenario(Scenario):
    """People who review each other's pull requests, with Open SWE assigning them."""

    def __init__(self, *, assignment_minutes: int = 120) -> None:
        super().__init__()
        self.assignment_minutes = assignment_minutes
        self.codeowners: list[str] = []
        self.habits: dict[str, _HabitRule] = {}
        self.author: Person | None = None
        self.files: list[str] = []
        self.repo_files: dict[str, str] = {}
        self.code_owner_review_required = False
        self.agent: Agent = TakesSuggestions()
        self.request: HumanReviewRequest | None = None
        self.coverage = Coverage(())
        self._acted: set[str] = set()
        self._directory: Directory | None = None
        self._github: GitHub | None = None
        self._slack: Slack | None = None
        self._decisions: Decisions | None = None

    # Describing the scenario.

    def owns(self, path: str, *owners: Person | str) -> None:
        """CODEOWNERS for ``path``; a plain string is a GitHub handle with no Open SWE account."""
        handles = (owner if isinstance(owner, str) else owner.login for owner in owners)
        self.codeowners.append(f"{path} " + " ".join(f"@{handle}" for handle in handles))

    def habit(
        self, person: Person, action: Habit, *, after: timedelta = timedelta(hours=1)
    ) -> None:
        """What ``person`` does ``after`` Open SWE picks them, as time passes."""
        self.habits[person.login] = _HabitRule(action, after)

    def reviewer_instructions(self, text: str) -> None:
        """The repository's ``.open-swe/REVIEWERS.md`` on its base branch."""
        self.repo_files[REVIEWER_INSTRUCTIONS_PATH] = text

    def requires_code_owner_review(self) -> None:
        """A ruleset on the base branch requires a code owner's approval for every owned file."""
        self.code_owner_review_required = True

    def pull_request(self, *, author: Person, files: list[str]) -> None:
        self.author = author
        self.files = files

    @property
    def narrator(self) -> Person:
        return self.author or super().narrator

    async def setup(self) -> None:
        if self.author is None:
            raise AssertionError("describe the pull request with scenario.pull_request() first")
        self._directory = Directory(self)
        self._github = GitHub(
            self,
            self.author,
            self.files,
            self.codeowners,
            author_user_id=self._directory[self.author.login].id,
            repo_files=self.repo_files,
            code_owner_review_required=self.code_owner_review_required,
        )
        self._slack = Slack(self, channels={"reviews": _REVIEWS}, describe_update=_card_reviewers)
        self._slack.wake = self._woken
        self._decisions = Decisions(self, _describe)
        self.coverage = await Coverage.build(self._github.parsed_codeowners, self.files)
        self.langgraph.handle("scheduler", self._deadline)

    def outside_world(self) -> list[Boundary]:
        return [
            boundary
            for boundary in (self._directory, self._github, self._slack, self._decisions)
            if boundary is not None
        ]

    def fakes(self) -> list[tuple[object, Fake]]:
        return [
            *super().fakes(),
            (routing.resolve_workspace, returning(_Workspace("acme"))),
            (dispatch.dispatch_agent_run, self._run_agent),
            (
                workspace_settings.get_workspace_settings,
                returning(_WorkspaceSettings(self.assignment_minutes)),
            ),
        ]

    def attribute_fakes(self) -> list[tuple[object, str, object]]:
        """The ``human_review_request`` table, kept in memory for the one request this scenario has."""
        return [
            (HumanReviewRequest, "save", instance_method(self._save)),
            (HumanReviewRequest, "locked", self._locked),
            (HumanReviewRequest, "get", self._get),
            (HumanReviewRequest, "active_for", self._active_for),
            (HumanReviewRequest, "open_review_counts", returning(Counter())),
        ]

    # What people do.

    @property
    def pr_url(self) -> str:
        if self._github is None:
            raise AssertionError("the scenario has not started")
        return self._github.url

    async def request_review(self) -> None:
        """The author's agent asks for a review in #reviews, as ``request_human_review`` does."""
        pr_ref = parse_github_pr_url(self.pr_url)
        if self._directory is None or self.author is None or pr_ref is None:
            raise AssertionError("the scenario has not started")
        self.record("action", "asks for a review in #reviews", source=self.author.login)
        origin = standard.Origin(
            requester=self._directory[self.author.login], thread_id="thread-implementation"
        )
        result = await standard.request_review(pr_ref, origin, channel="#reviews")
        if not result.success:
            raise AssertionError(f"request_review refused: {result.error}")

    @property
    def card_offers_signup(self) -> bool:
        """Whether the review card still shows I'll review; it goes once the card collapses."""
        slack = self._slack
        if slack is None or self.request is None:
            return False
        return slack.message(_REVIEWS, self.request.slack_message_ts).offers(_SIGN_UP)

    async def clicks(self, person: Person, button: Button, choice: str = "") -> None:
        """``person`` clicks I'll review on the card or a button on their pick DM in Slack.

        Decline and Snooze open a modal, answered with ``choice``.
        """
        slack = self._slack
        if slack is None:
            raise AssertionError("the scenario has not started")
        self.record("action", f"clicks {_BUTTONS[button]}", source=person.login)
        if button == "ill_review":
            card = slack.message(_REVIEWS, self.review_request.slack_message_ts)
            await slack.click(person, card, _SIGN_UP)
            return
        dm = slack.last_dm(person, _PICK_NOTICES)
        await slack.click(person, dm, f"open_swe_option_select_{button}")
        if button in ("decline", "snooze"):
            await slack.choose(person, choice)

    async def use_tool(
        self, thread_id: str, tool: object, **arguments: JsonValue
    ) -> dict[str, JsonValue]:
        """Call an agent tool as the agent running ``thread_id`` would."""
        structured = StructuredTool.from_function(coroutine=tool)  # type: ignore[arg-type]
        result = await structured.ainvoke(
            arguments, config={"configurable": {"thread_id": thread_id}}
        )
        return result if isinstance(result, dict) else {"result": str(result)}

    async def reviews_on_github(self, person: Person, state: ReviewState = "APPROVED") -> None:
        if self._github is None:
            raise AssertionError("the scenario has not started")
        self._github.review(person, state)
        await standard.settle_pull_request(
            self._github.owner, self._github.repo, self._github.number
        )

    async def tick(self) -> None:
        """People act on their picks as their habits say."""
        request = self.request
        if request is None or request.state != "open" or self._directory is None:
            return
        for participant in [*request.picks, *request.reviewers]:
            person = self.people[self._directory.login_by_id[participant.user_id]]
            habit = self.habits.get(person.login)
            if habit is None or habit.action == "ignore" or participant.joined_at is None:
                continue
            if self.now - participant.joined_at < habit.after:
                continue
            key = f"{person.login}:{participant.joined_at.isoformat()}:{participant.decision}"
            if key in self._acted:
                continue
            self._acted.add(key)
            if participant.decision == "picked" and habit.action in (
                "accept",
                "accept_then_approve",
            ):
                await self.clicks(person, "accept")
            elif participant.decision == "picked" and habit.action == "decline":
                await self.clicks(person, "decline", "Away or unavailable")
            elif habit.action == "approve" or (
                habit.action == "accept_then_approve" and participant.decision == "review"
            ):
                await self.reviews_on_github(person)

    # Open SWE's side of the world.

    @property
    def review_request(self) -> HumanReviewRequest:
        if self.request is None:
            raise AssertionError("nobody has asked for a review yet")
        return self.request

    async def _deadline(self, input: dict[str, JsonValue]) -> str:
        step = str(input["step"])
        result = await standard.run_deadline(str(input["request_id"]), step)
        return f"{step.split(':')[0]} → {result['status']}"

    async def _woken(self, thread_id: str, prompt: str) -> None:
        await self.agent.woken(self, thread_id, prompt)

    async def _run_agent(
        self, thread_id: str, content: object, configurable: object, **_: object
    ) -> None:
        """A run queued on an agent thread, answered by the scenario's agent."""
        if not isinstance(content, str):
            raise AssertionError(f"unexpected run input for {thread_id}: {content!r}")
        await self.agent.woken(self, thread_id, content)

    async def _save(self, request: HumanReviewRequest) -> HumanReviewRequest:
        if self._github is None or self._directory is None:
            raise AssertionError("the scenario has not started")
        if request.created_at is None:
            request.created_at = self.now
        request.pull_request = self._github.row
        if request.requested_by_user_id is not None:
            request.requested_by = self._directory[
                self._directory.login_by_id[request.requested_by_user_id]
            ]
        self.request = request
        return request

    @asynccontextmanager
    async def _locked(
        self, request_id: UUID
    ) -> AsyncIterator[tuple[None, HumanReviewRequest | None]]:
        request = self.request if self.request and self.request.id == request_id else None
        yield None, request
        if request is None or self._directory is None:
            return
        for participant in request.participants:
            if participant.joined_at is None:
                participant.joined_at = self.now
            if getattr(participant, "user", None) is None:
                participant.user = self._directory[self._directory.login_by_id[participant.user_id]]

    async def _get(self, request_id: UUID) -> HumanReviewRequest | None:
        return self.request if self.request and self.request.id == request_id else None

    async def _active_for(self, owner: str, repo: str, number: int) -> HumanReviewRequest | None:
        return self.request if self.request and self.request.state == "open" else None

    # Rules every play keeps.

    def _decisions_of(
        self, kind: str, cause: ReviewDecisionCause | None = None
    ) -> list[tuple[int, list[str]]]:
        return [
            (
                index,
                [str(r) for r in reviewers]
                if isinstance(reviewers := m.data.get("reviewers"), list)
                else [],
            )
            for index, m in enumerate(self.timeline)
            if m.kind == "decision"
            and m.data.get("decision") == kind
            and (cause is None or m.data.get("cause") == cause)
        ]

    @invariant
    def no_dm_is_deleted(self) -> list[str]:
        """A DM is edited to say why it no longer applies, never deleted."""
        return [
            f"a DM to {m.target} was deleted instead of edited"
            for m in self.timeline
            if m.kind == "delete" and m.ref.startswith("D_")
        ]

    @invariant
    def only_accepted_reviewers_are_reminded(self) -> list[str]:
        """Nobody is reminded to submit a review they never accepted."""
        accepted = {
            login
            for _, logins in self._decisions_of("reviewer_joined", "accepted_pick")
            for login in logins
        }
        return [
            f"{m.target} was reminded to submit a review they never accepted"
            for m in self.timeline
            if m.kind == "message" and m.label == "review_reminder" and m.target not in accepted
        ]

    @invariant
    def released_picks_lose_their_buttons(self) -> list[str]:
        """Once someone is released, every pick DM they got has been edited."""
        found: list[str] = []
        for index, logins in self._decisions_of("reviewers_released"):
            for login in logins:
                for sent_at, sent in enumerate(self.timeline[:index]):
                    if (
                        sent.kind != "message"
                        or sent.target != login
                        or sent.label not in _PICK_NOTICES
                    ):
                        continue
                    if not any(
                        m.kind == "edit" and m.ref == sent.ref for m in self.timeline[sent_at:]
                    ):
                        found.append(f"{login} was released but a pick DM kept its buttons")
        return found

    @invariant
    def the_author_is_never_picked(self) -> list[str]:
        """The author is never picked to review their own pull request."""
        author = self.author.login if self.author else ""
        return [
            "the author was picked for their own pull request"
            for _, logins in self._decisions_of("reviewer_picked")
            if author in logins
        ]

    @invariant
    def nobody_is_notified_outside_work_hours(self) -> list[str]:
        """Nobody gets a DM or a GitHub review request outside their own work hours.

        Someone who signed up themselves asked for the review, so theirs may come any time.
        """
        volunteers = {
            login
            for _, logins in self._decisions_of("reviewer_joined", "signed_up")
            for login in logins
        }
        return [
            f"{m.target} was notified outside their work hours "
            f"({self.people[m.target].local(m.at)}): {m.label or 'message'}"
            for m in self.timeline
            if m.kind == "message"
            and not self.people[m.target].on_shift(m.at)
            and not (m.label == GITHUB_REVIEW_REQUEST and m.target in volunteers)
        ]

    @invariant
    def the_author_hears_once_per_overdue_set(self) -> list[str]:
        """The author hears at most once that the same reviewers are overdue."""
        counts = Counter(tuple(logins) for _, logins in self._decisions_of("review_overdue"))
        return [
            f"the author was told {count} times that {', '.join(who)} is overdue"
            for who, count in counts.items()
            if count > 1
        ]

    @invariant
    def rotation_stops_after_two_picks(self) -> list[str]:
        """Open SWE rotates away from at most one lapsed pick per code owner area.

        A decline is the reviewer's own choice and is replaced, so it does not count.
        """
        lapsed = [
            login
            for _, logins in self._decisions_of("reviewers_released", "expired")
            for login in logins
        ]
        found: list[str] = []
        for area in self.coverage.areas:
            here = [login for login in lapsed if self.coverage.of(login) == [area]]
            if len(here) > 1:
                found.append(
                    f"Open SWE rotated past {len(here)} lapsed picks for {' '.join(area.handles)}"
                )
        return found


_BUTTONS: dict[Button, str] = {
    "ill_review": "I'll review on the card",
    "accept": "Accept on the pick DM",
    "decline": "Decline on the pick DM",
    "snooze": "Snooze on the pick DM",
}


def _describe(event_type: str, payload: dict[str, JsonValue]) -> str:
    reviewers = payload.get("reviewers")
    who = ", ".join(str(r) for r in reviewers) if isinstance(reviewers, list) else ""
    line = " ".join(part for part in (str(payload.get("decision", event_type)), who) if part)
    cause = payload.get("cause")
    return f"{line} ({cause})" if cause else line


def _card_reviewers(channel: str, blocks: list[dict[str, JsonValue]] | None) -> str:
    for block in blocks or []:
        text = block.get("text")
        if isinstance(text, dict) and "*Reviewers*" in str(text.get("text", "")):
            reviewers = str(text["text"]).replace("*Reviewers*\n", "").replace("\n", " · ")
            return f"card · {reviewers}"
    return ""
