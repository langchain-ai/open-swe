"""The ``Robin Approves`` check: Robin's one gate on whether a pull request may merge yet.

Robin posts this check only where the target branch requires it, so anything Robin
decides should hold back a merge goes through it, whoever has approved. Its first
rule closes a gap in GitHub's own: GitHub never lets a pull request's author approve
it, but when Robin authors a pull request as itself (an Ask channel) the person who
asked for it is not the author. Such a pull request passes
once someone approved its current head who neither asked for the change nor pushed
to it, or once Robin's auto-approval approved that head. Pull requests a person
or another bot authored pass, leaving them to the repository's review rules.

Hard CODEOWNERS review and rules repositories configure themselves are meant to
become further reasons this same check fails.

The check blocks a merge only where the repository's rulesets require it, so Robin
refuses to author a pull request as itself anywhere they don't
(``Repository.requires_approval_check``).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from openswe.config import ENV
from openswe.github.checks import APPROVAL_CHECK_RUN_NAME
from openswe.github.org_membership import OPEN_SWE_GITHUB_LOGINS
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.users import User

logger = logging.getLogger(__name__)

# Who GitHub names as the committer of commits made in its web UI.
_GITHUB_COMMITTER = "web-flow"
EVALUATE_JOB = "robin_approves_evaluate"
_RETRY_DELAY = timedelta(minutes=1)
_NOTE_MARKER = "<!-- robin-approves-note -->"
_STANDING_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
# Robin can't read a ruleset's bypass list, so this says where the bypass shows up.
_BREAK_GLASS = (
    "<details>\n<summary>Break glass</summary>\n\n"
    "For urgent merges or a Robin outage, someone on the required ruleset's bypass list "
    "can select **bypass rules** in the PR's merge box. GitHub records who bypassed the check."
    "\n\n</details>"
)


@dataclass(frozen=True, slots=True)
class PullRequestApproval:
    """One pull request head's approvals, and who may not give them."""

    author: Mapping[str, Any]
    head_sha: str
    requesters: frozenset[str] = frozenset()
    requester_ids: frozenset[int] = frozenset()
    # Robin's auto-approval approved this head, so the author alone may merge it.
    auto_approved: bool = False
    # Opened before requesters were kept, and nobody knows who asked for it.
    requester_unknown: bool = False
    reviews: list[dict[str, Any]] = field(default_factory=list)
    commits: list[dict[str, Any]] = field(default_factory=list)
    # Human reviewers with write access, whose approvals GitHub would count.
    writers: frozenset[str] = frozenset()

    @staticmethod
    def _login(user: object) -> str:
        login = user.get("login") if isinstance(user, Mapping) else None
        return login.lower() if isinstance(login, str) else ""

    @staticmethod
    def _is_bot(user: object) -> bool:
        return isinstance(user, Mapping) and user.get("type") == "Bot"

    @classmethod
    def _is_robin(cls, user: object) -> bool:
        return cls._is_bot(user) and cls._login(user) in OPEN_SWE_GITHUB_LOGINS

    @classmethod
    async def read(cls, pull: PullRequestClient, payload: Mapping[str, Any]) -> Self:
        author, head_sha = payload.get("user") or {}, (payload.get("head") or {}).get("sha", "")
        if not cls._is_robin(author):
            return cls(author=author, head_sha=head_sha)
        reviews = await pull.reviews()
        stored = await PullRequest.get(pull.repo.owner, pull.repo.name, pull.number)
        requesters, requester_ids = await PullRequest.requesters(
            pull.repo.owner, pull.repo.name, pull.number
        )
        humans = {
            cls._login(review.get("user"))
            for review in reviews
            if review.get("state") == "APPROVED" and not cls._is_bot(review.get("user"))
        } - {""}
        writers = {login for login in humans if await pull.repo.can_write(login)}
        return cls(
            author=author,
            head_sha=head_sha,
            writers=frozenset(writers),
            requesters=frozenset(requesters),
            requester_ids=frozenset(requester_ids),
            auto_approved=bool(head_sha)
            and stored is not None
            and stored.auto_approved_head_sha == head_sha,
            requester_unknown=stored is not None and stored.requester_unknown,
            reviews=reviews,
            commits=await pull.contribution_commits((payload.get("base") or {}).get("sha", "")),
        )

    @property
    def excluded(self) -> set[str]:
        """The requesters and contributors, excluding merges of the PR's base branch."""
        logins = {
            self._login(commit.get(role))
            for commit in self.commits
            for role in ("author", "committer")
        }
        logins |= {login.lower() for login in self.requesters}
        return logins - {"", _GITHUB_COMMITTER}

    def _approvals(self) -> dict[str, Mapping[str, Any]]:
        """Each reviewer's standing approval, ignoring reviews they later withdrew."""
        latest: dict[str, Mapping[str, Any]] = {}
        for review in self.reviews:
            if review.get("state") in _STANDING_STATES:
                latest[self._login(review.get("user"))] = review
        return {
            login: review
            for login, review in latest.items()
            if login and review.get("state") == "APPROVED"
        }

    def _rejection(self, login: str, review: Mapping[str, Any]) -> str | None:
        """Why ``login``'s approval doesn't count, or ``None`` when it does."""
        user = review.get("user") or {}
        if login in {name.lower() for name in self.requesters} or (
            isinstance(user, Mapping) and user.get("id") in self.requester_ids
        ):
            return "asked Robin for this change"
        if login in self.excluded:
            return "authored or committed a commit on this branch"
        if self._is_bot(user):
            return None if login == self._login(self.author) else "is a bot"
        if review.get("commit_id") != self.head_sha:
            return "approved an earlier commit; re-approve the latest commit"
        if login not in self.writers:
            return "doesn't have write access to this repository"
        return None

    def approvers(self) -> list[str]:
        return sorted(
            login
            for login, review in self._approvals().items()
            if self._rejection(login, review) is None and review.get("commit_id") == self.head_sha
        )

    def verdict(self) -> tuple[bool, str]:
        """Whether the pull request may merge, and why, in words for the people waiting on it."""
        if not self._is_robin(self.author):
            return (
                True,
                "Robin did not author this pull request; the repository's review rules apply.",
            )
        if self.auto_approved:
            return True, "Robin's auto-approval approved the current head."
        if self.requester_unknown:
            return False, (
                "Robin opened this pull request before it kept track of who asked for it, so it "
                "can't tell whose approval to refuse. Ask Robin to open it again, or have "
                "Robin's auto-approval approve it."
            )
        if approvers := self.approvers():
            return True, f"Approved at the current head by {', '.join(approvers)}."
        lines = [
            "Robin opened this PR, so it needs approval of the latest commit from someone with "
            "write access who neither requested this change nor authored or committed to this branch."
        ]
        rejected = {
            login: reason
            for login, review in self._approvals().items()
            if (reason := self._rejection(login, review))
        }
        if rejected:
            lines.append(
                "**Approvals not counted**\n\n"
                + "\n".join(f"- `{login}` {reason}." for login, reason in sorted(rejected.items()))
            )
        else:
            lines.append("No approval of the latest commit yet.")
        return False, "\n\n".join(lines)

    @classmethod
    async def evaluate(
        cls, owner: str, repo: str, number: int, *, force_publish: bool = False
    ) -> tuple[bool, str] | None:
        """Post the approval verdict when enabled and required or explicitly requested."""
        if ENV.DISABLE_ROBIN_APPROVES.get_bool():
            return
        async with PullRequestClient.as_app(owner, repo, number) as pull:
            payload = await pull.pull()
            if payload.get("state") != "open":
                return
            base = (payload.get("base") or {}).get("ref")
            if not base or (
                not force_publish
                and await Repository.requires_approval_check(
                    owner, repo, base, include_any_source=True
                )
                is not True
            ):
                return
            approval = await cls.read(pull, payload)
            if not approval.head_sha:
                return
            passed, summary = approval.verdict()
            if not passed:
                summary = f"{summary}\n\n{_BREAK_GLASS}"
            await pull.repo.create_check_run(
                {
                    "name": APPROVAL_CHECK_RUN_NAME,
                    "head_sha": approval.head_sha,
                    "status": "completed",
                    "conclusion": "success" if passed else "failure",
                    "completed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "output": {
                        "title": "Ready to merge" if passed else "Not ready to merge",
                        "summary": summary,
                    },
                }
            )
            await cls._update_note(pull, passed, summary)
            return passed, summary

    @staticmethod
    async def _update_note(pull: PullRequestClient, passed: bool, summary: str) -> None:
        """Keep one comment on the pull request saying why the check fails, edited as it changes.

        Nothing is posted while the check has always passed.
        """
        body = (
            f"{_NOTE_MARKER}\n**`{APPROVAL_CHECK_RUN_NAME}` "
            f"{'passes' if passed else 'does not pass'}.**\n\n{summary}\n\n"
            f"_`{APPROVAL_CHECK_RUN_NAME}` reports the latest evaluation of Robin's approval policy. "
            "Repository rules determine whether this check is required to merge._"
        )
        try:
            note = next(
                (
                    comment
                    for comment in await pull.issue_comments()
                    if _NOTE_MARKER in str(comment.get("body") or "")
                    and (comment.get("user") or {}).get("type") == "Bot"
                ),
                None,
            )
            if note is None:
                if not passed:
                    await pull.comment(body)
            elif note.get("body") != body and isinstance(note.get("id"), int):
                await pull.edit_comment(note["id"], body)
        except Exception:
            logger.exception(
                "Failed to update the approval note",
                extra={"pr_repo_full_name": pull.repo.full_name, "pr_number": pull.number},
            )

    @classmethod
    async def add_requesters(cls, owner: str, repo: str, number: int, logins: set[str]) -> None:
        """Add who asked for an Ask PR, all or none; raises when they can't be recorded."""
        people = {}
        for login in sorted(logins):
            user = await User.for_login("github", login)
            people[login] = user.id if user else None
        await PullRequest(owner=owner, repo=repo, number=number).add_requesters(people)

    @classmethod
    async def record_requesters(cls, owner: str, repo: str, number: int, logins: set[str]) -> bool:
        """Add who asked for an Ask PR, then post the check; ``False`` if they weren't recorded."""
        try:
            await cls.add_requesters(owner, repo, number, logins)
        except Exception:
            logger.exception(
                "Failed to record the Ask PR requester",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            )
            return False
        try:
            await cls.evaluate(owner, repo, number)
        except Exception:
            logger.exception(
                "Failed to post the approval check; retrying from the job queue",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            )
            await cls.queue_evaluation(owner, repo, number)
        return True

    @classmethod
    async def record_auto_approval(cls, owner: str, repo: str, number: int, head_sha: str) -> None:
        """Let Robin's auto-approval of ``head_sha`` pass the check (``""`` withdraws it), then post it."""
        try:
            await PullRequest.record_auto_approval(owner, repo, number, head_sha)
        except Exception:
            logger.exception(
                "Failed to record Robin's auto-approval",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            )
            return
        try:
            await cls.evaluate(owner, repo, number)
        except Exception:
            await cls.queue_evaluation(owner, repo, number)
            logger.exception(
                "Failed to record Robin's auto-approval",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            )

    @staticmethod
    async def queue_evaluation(owner: str, repo: str, number: int) -> None:
        """Post the check again from the job queue, which retries with backoff until it lands."""
        await queue.coalesce(
            EVALUATE_JOB,
            {"owner": owner, "repo": repo, "number": number},
            key=f"{EVALUATE_JOB}:{owner}/{repo}#{number}".lower(),
            due_at=datetime.now(UTC) + _RETRY_DELAY,
        )

    @classmethod
    async def evaluate_job(cls, job: Job) -> None:
        """The queued retry: raises, so the queue retries, until the check is posted."""
        owner, repo, number = (job.payload.get(name) for name in ("owner", "repo", "number"))
        if isinstance(owner, str) and isinstance(repo, str) and isinstance(number, int):
            await cls.evaluate(owner, repo, number)

    @classmethod
    async def evaluate_payload(cls, payload: Mapping[str, Any]) -> None:
        """Re-evaluate the pull request a ``pull_request`` or ``pull_request_review`` names."""
        repository = payload.get("repository") or {}
        number = (payload.get("pull_request") or {}).get("number")
        owner, repo = (repository.get("owner") or {}).get("login"), repository.get("name")
        if isinstance(number, int) and isinstance(owner, str) and isinstance(repo, str):
            await cls.evaluate(owner, repo, number)
