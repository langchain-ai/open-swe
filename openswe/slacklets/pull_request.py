"""A pull request slacklet's state and the typed commands that transform it.

State changes only by applying commands, in order: replaying a slacklet's
commands from the first rebuilds its state exactly. GitHub events never become
commands directly; they prompt a fresh read of the pull request, and
:meth:`PullRequestView.commands_to` turns the difference into commands.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from openswe.github.ci import FAILING_CONCLUSIONS, CommitChecks
from openswe.github.comments import derive_pr_state
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import PullRequestReviewer
from openswe.slack.blocks import Block, context, escape, section
from openswe.slack.client import GitHubPrRef

PullRequestStatus = Literal["draft", "open", "merged", "closed"]
CiStatus = Literal["none", "pending", "passing", "failing"]
Verdict = Literal["approved", "changes_requested", "commented", "dismissed"]

_FAILING_STATUSES = frozenset({"failure", "error"})


class StaleCommand(ValueError):
    """A command addressed to a head commit the slacklet has moved past."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Ci(_Frozen):
    sha: str
    status: CiStatus
    failed: tuple[str, ...] = ()

    @classmethod
    def of(cls, sha: str, checks: CommitChecks | None) -> Self:
        """CI on ``sha``: failing once anything failed, pending while anything runs."""
        if checks is None or not (checks.runs or checks.statuses):
            return cls(sha=sha, status="none")
        failed = sorted(
            {
                str(run.get("name") or "")
                for run in checks.runs
                if run.get("status") == "completed" and run.get("conclusion") in FAILING_CONCLUSIONS
            }
            | {
                str(status.get("context") or "")
                for status in checks.statuses
                if status.get("state") in _FAILING_STATUSES
            }
        )
        if failed:
            return cls(sha=sha, status="failing", failed=tuple(failed))
        running = any(run.get("status") != "completed" for run in checks.runs) or any(
            status.get("state") == "pending" for status in checks.statuses
        )
        return cls(sha=sha, status="pending" if running else "passing")


class PullRequestView(_Frozen):
    """What a pull request slacklet shows, and what a read of GitHub observed."""

    url: str
    repo: str
    number: int
    title: str
    status: PullRequestStatus
    ci: Ci
    reviews: dict[str, Verdict] = {}

    @property
    def key(self) -> PullRequestKey:
        owner, repo = self.repo.split("/", 1)
        return PullRequestKey.of(owner, repo, self.number)

    @classmethod
    def observe(
        cls,
        ref: GitHubPrRef,
        *,
        pull: dict[str, object],
        checks: CommitChecks | None,
        reviewers: list[PullRequestReviewer] | None,
    ) -> Self:
        head = pull.get("head")
        sha = str(head.get("sha") or "") if isinstance(head, dict) else ""
        return cls(
            url=ref.url,
            repo=f"{ref.owner}/{ref.repo}",
            number=ref.number,
            title=str(pull.get("title") or ""),
            status=derive_pr_state(
                state=str(pull.get("state") or ""),
                merged=bool(pull.get("merged")),
                draft=bool(pull.get("draft")),
            ),
            ci=Ci.of(sha, checks),
            reviews={reviewer.login: reviewer.state for reviewer in reviewers or []},
        )

    def commands_to(self, observed: PullRequestView) -> list[Command]:
        """The commands that take this view to ``observed``; none when nothing changed."""
        commands: list[Command] = []
        if observed.title != self.title:
            commands.append(SetTitle(title=observed.title))
        if observed.status != self.status:
            commands.append(SetStatus(status=observed.status))
        ci = self.ci
        if observed.ci.sha != ci.sha:
            commands.append(SetHead(sha=observed.ci.sha))
            ci = Ci(sha=observed.ci.sha, status="pending")
        if observed.ci != ci:
            commands.append(
                SetCi(sha=observed.ci.sha, status=observed.ci.status, failed=observed.ci.failed)
            )
        for reviewer, verdict in sorted(observed.reviews.items()):
            if self.reviews.get(reviewer) != verdict:
                commands.append(SetReview(reviewer=reviewer, verdict=verdict))
        return commands

    def blocks(self, *, seq: int, last: Command) -> list[Block]:
        """The slacklet message for this state, reached by command ``seq``."""
        ci = _CI_LABELS[self.ci.status]
        if self.ci.failed:
            ci = f"{ci}: {escape(', '.join(self.ci.failed))}"
        lines = [
            f"*<{self.url}|{escape(self.repo)}#{self.number}>* · {escape(self.title)}",
            f"{_STATUS_LABELS[self.status]}   ·   {ci}   ·   head `{self.ci.sha[:7]}`",
        ]
        if self.reviews:
            lines.append(
                "Reviews: "
                + "   ".join(
                    f"{_VERDICT_ICONS[verdict]} {escape(reviewer)}"
                    for reviewer, verdict in sorted(self.reviews.items())
                )
            )
        return [
            section("\n".join(lines)),
            context(f"Pull request slacklet · state after #{seq} {last.op}"),
        ]

    def fallback_text(self) -> str:
        return (
            f"{self.repo}#{self.number} {self.title}: {self.status}, "
            f"{_CI_SUMMARIES[self.ci.status]}"
        )


_STATUS_LABELS: dict[PullRequestStatus, str] = {
    "draft": "⚪ *Draft*",
    "open": "🟢 *Open*",
    "merged": "🟣 *Merged*",
    "closed": "🔴 *Closed*",
}
_CI_LABELS: dict[CiStatus, str] = {
    "none": "▫️ No CI",
    "pending": "🟡 CI running",
    "passing": "✅ CI passing",
    "failing": "❌ CI failing",
}
_VERDICT_ICONS: dict[Verdict, str] = {
    "approved": "✅",
    "changes_requested": "🔁",
    "commented": "💬",
    "dismissed": "➖",
}


class Attach(_Frozen):
    """Start tracking: the whole pull request as first read."""

    op: Literal["attach"] = "attach"
    view: PullRequestView

    def apply(self, view: PullRequestView | None) -> PullRequestView:
        if view is not None:
            raise ValueError("The slacklet is already attached")
        return self.view

    def summary(self) -> str:
        return f"Tracking {self.view.repo}#{self.view.number}: “{self.view.title}”"


class SetTitle(_Frozen):
    op: Literal["set_title"] = "set_title"
    title: str

    def apply(self, view: PullRequestView) -> PullRequestView:
        return view.model_copy(update={"title": self.title})

    def summary(self) -> str:
        return f"Retitled to “{self.title}”"


_STATUS_SUMMARIES: dict[PullRequestStatus, str] = {
    "draft": "Converted to draft",
    "open": "Open for review",
    "merged": "Merged",
    "closed": "Closed without merging",
}


class SetStatus(_Frozen):
    op: Literal["set_status"] = "set_status"
    status: PullRequestStatus

    def apply(self, view: PullRequestView) -> PullRequestView:
        return view.model_copy(update={"status": self.status})

    def summary(self) -> str:
        return _STATUS_SUMMARIES[self.status]


class SetHead(_Frozen):
    """New commits: CI starts over on the new head."""

    op: Literal["set_head"] = "set_head"
    sha: str

    def apply(self, view: PullRequestView) -> PullRequestView:
        return view.model_copy(update={"ci": Ci(sha=self.sha, status="pending")})

    def summary(self) -> str:
        return f"New head {self.sha[:7]}; CI restarted"


_CI_SUMMARIES: dict[CiStatus, str] = {
    "none": "No CI reported",
    "pending": "CI running",
    "passing": "CI passing",
    "failing": "CI failing",
}


class SetCi(_Frozen):
    op: Literal["set_ci"] = "set_ci"
    sha: str
    status: CiStatus
    failed: tuple[str, ...] = ()

    def apply(self, view: PullRequestView) -> PullRequestView:
        if self.sha != view.ci.sha:
            raise StaleCommand(f"CI for {self.sha[:7]} is not for head {view.ci.sha[:7]}")
        return view.model_copy(
            update={"ci": Ci(sha=self.sha, status=self.status, failed=self.failed)}
        )

    def summary(self) -> str:
        failed = f": {', '.join(self.failed)}" if self.failed else ""
        return f"{_CI_SUMMARIES[self.status]} on {self.sha[:7]}{failed}"


_VERDICT_SUMMARIES: dict[Verdict, str] = {
    "approved": "approved",
    "changes_requested": "requested changes",
    "commented": "commented",
    "dismissed": "had their review dismissed",
}


class SetReview(_Frozen):
    op: Literal["set_review"] = "set_review"
    reviewer: str
    verdict: Verdict

    def apply(self, view: PullRequestView) -> PullRequestView:
        return view.model_copy(update={"reviews": {**view.reviews, self.reviewer: self.verdict}})

    def summary(self) -> str:
        return f"{self.reviewer} {_VERDICT_SUMMARIES[self.verdict]}"


type Command = Annotated[
    Attach | SetTitle | SetStatus | SetHead | SetCi | SetReview, Field(discriminator="op")
]
COMMAND = TypeAdapter[Command](Command)


def apply(view: PullRequestView | None, command: Command) -> PullRequestView:
    """``view`` after ``command``; only ``attach`` applies to a slacklet with no state."""
    if isinstance(command, Attach):
        return command.apply(view)
    if view is None:
        raise ValueError(f"{command.op} needs an attached slacklet")
    return command.apply(view)
