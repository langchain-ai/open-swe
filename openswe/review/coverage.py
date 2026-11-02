"""Which of a pull request's changed lines people have reviewed, and whether its code owners have.

A line is reviewed by whoever marked it: "Looks good" on a walkthrough chunk, or
"Viewed" on the review page. Both are kept per person as ``ReviewGuideSeen``
line ranges at one head. A code owner's GitHub approval covers every file they
own, and any approval covers a file nobody owns. Files under a CODEOWNERS rule
marked ``@hard-required-review`` count only their owners' review.
"""

import asyncio
from dataclasses import dataclass
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel

from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import latest_review_states
from openswe.github.codeowners import CodeOwners
from openswe.github.pull_request_diff import resolve_merge_base
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.human_review.picking import Area, Coverage
from openswe.review_guide.sessions import ReviewGuideSeen
from openswe.users.models import User
from openswe.walkthrough.lines import LineSet, Ranges

CoverageState = Literal["owner_reviewed", "reviewed", "partial", "unreviewed"]
_MAX_FILE_PAGES = 30


class LineReviewer(BaseModel):
    login: str
    lines: int


class FileCoverage(BaseModel):
    path: str
    changed_lines: int
    code_owners: list[str]
    hard: bool
    # GitHub approvals that cover the file: its owners', or anyone's when it has none.
    approved_by: list[str]
    reviewers: list[LineReviewer]
    reviewed_lines: int
    owner_reviewed_lines: int
    state: CoverageState


class ReviewCoverage(BaseModel):
    head_sha: str
    files: list[FileCoverage]
    truncated: bool


class HunkRange(BaseModel):
    """One hunk as the review page draws it, context lines included."""

    addition_start: int
    addition_count: int
    deletion_start: int
    deletion_count: int


class ReviewedFile(BaseModel):
    path: str
    # Only these hunks of the file; all of it when omitted.
    hunks: list[HunkRange] | None = None


class StaleHeadError(Exception):
    """The pull request moved past the head the reviewer was reading."""


@dataclass(frozen=True, slots=True)
class PullRequestLines:
    """A pull request's changed lines at its current head, as GitHub lists them."""

    owner: str
    repo: str
    number: int
    head_sha: str
    base_sha: str
    base_ref: str
    author: str
    files: list[ChangedFile]
    truncated: bool

    @classmethod
    async def fetch(cls, owner: str, repo: str, number: int) -> Self:
        async with PullRequestClient.as_app(owner, repo, number) as pull:
            payload = await pull.pull()
            changed = payload.get("changed_files")
            files = await ChangedFile.of_pull(
                pull,
                max_pages=_MAX_FILE_PAGES,
                count=changed if isinstance(changed, int) else None,
            )
        if files is None:
            raise ValueError("GitHub did not list the pull request's changed files")
        base = payload.get("base") or {}
        head = payload.get("head") or {}
        user = payload.get("user") or {}
        return cls(
            owner=owner,
            repo=repo,
            number=number,
            head_sha=str(head.get("sha") or ""),
            base_sha=str(base.get("sha") or ""),
            base_ref=str(base.get("ref") or ""),
            author=str(user.get("login") or ""),
            files=files,
            truncated=isinstance(changed, int) and changed > len(files),
        )

    async def record(self) -> PullRequest:
        return await (await PullRequest.load(self.owner, self.repo, self.number)).ensure()

    def selected(self, reviewed: list[ReviewedFile]) -> LineSet:
        """The changed lines ``reviewed`` names, at this head."""
        by_path = {file.filename: file for file in self.files}
        picked: list[LineSet] = []
        for choice in reviewed:
            file = by_path.get(choice.path)
            if file is None:
                continue
            lines = file.lines
            if choice.hunks is not None:
                lines &= LineSet(
                    {
                        choice.path: {
                            "+": Ranges.of(
                                (h.addition_start, h.addition_start + h.addition_count - 1)
                                for h in choice.hunks
                            ),
                            "-": Ranges.of(
                                (h.deletion_start, h.deletion_start + h.deletion_count - 1)
                                for h in choice.hunks
                            ),
                        }
                    }
                )
            picked.append(lines)
        return LineSet.union(picked)

    async def mark(
        self, user_id: UUID, head_sha: str, reviewed: list[ReviewedFile], *, done: bool
    ) -> None:
        """Record that this person reviewed ``reviewed`` at ``head_sha``, or take it back."""
        if head_sha != self.head_sha:
            raise StaleHeadError(head_sha)
        lines = self.selected(reviewed)
        if not lines:
            return
        record = await self.record()
        async with PullRequestClient.as_app(self.owner, self.repo, self.number) as pull:
            merge_base = await resolve_merge_base(pull.repo, self.base_sha, self.head_sha)

        def change(row: ReviewGuideSeen | None) -> LineSet:
            kept = row.lines if row is not None and row.head_sha == self.head_sha else LineSet()
            return kept | lines if done else kept - lines

        await ReviewGuideSeen.update(
            user_id, record.id, self.head_sha, merge_base, change, by=user_id
        )

    async def coverage(self) -> ReviewCoverage:
        async with PullRequestClient.as_app(self.owner, self.repo, self.number) as pull:
            states, codeowners = await asyncio.gather(
                latest_review_states(pull, self.author),
                CodeOwners.fetch(pull.repo, self.base_sha or self.base_ref or None),
            )
        approvers = [login for login, state in (states or {}).items() if state == "APPROVED"]
        paths = [file.filename for file in self.files]
        owned = await Coverage.build(codeowners, paths) if codeowners else Coverage(())
        area_of = {path: area for area in owned.areas for path in area.files}
        record = await PullRequest.get(self.owner, self.repo, self.number)
        rows = await ReviewGuideSeen.at_head(record.id, self.head_sha) if record else []
        people = await asyncio.gather(*(User.get(row.user_id) for row in rows))
        marks = [
            (person.github_login.lower() or person.login_for("github"), row.lines)
            for row, person in zip(rows, people, strict=True)
            if person is not None
        ]
        return ReviewCoverage(
            head_sha=self.head_sha,
            files=[
                _FileReview(file, area_of.get(file.filename), approvers, marks).coverage()
                for file in self.files
            ],
            truncated=self.truncated,
        )


@dataclass(frozen=True, slots=True)
class _FileReview:
    file: ChangedFile
    area: Area | None
    approvers: list[str]
    marks: list[tuple[str, LineSet]]

    def owns(self, login: str) -> bool:
        return self.area is None or login.lower() in self.area.owners

    def coverage(self) -> FileCoverage:
        path = self.file.filename
        changed = self.file.lines
        total = len(changed)
        approved_by = [login for login in self.approvers if self.owns(login)]
        reviewers: list[LineReviewer] = []
        reviewed: list[LineSet] = []
        by_owner: list[LineSet] = []
        for login, lines in self.marks:
            mine = lines.only([path]) & changed
            if not mine:
                continue
            reviewers.append(LineReviewer(login=login, lines=len(mine)))
            reviewed.append(mine)
            if self.area is not None and self.owns(login):
                by_owner.append(mine)
        reviewers.sort(key=lambda reviewer: -reviewer.lines)
        if approved_by:
            reviewed_lines = owner_lines = total
        else:
            reviewed_lines = len(LineSet.union(reviewed))
            owner_lines = len(LineSet.union(by_owner))
        state: CoverageState
        if approved_by or (self.area is not None and total and owner_lines == total):
            state = "owner_reviewed"
        elif total and reviewed_lines == total:
            state = "reviewed"
        elif reviewed_lines:
            state = "partial"
        else:
            state = "unreviewed"
        return FileCoverage(
            path=path,
            changed_lines=total,
            code_owners=list(self.area.handles) if self.area else [],
            hard=self.area.hard if self.area else False,
            approved_by=approved_by,
            reviewers=reviewers,
            reviewed_lines=reviewed_lines,
            owner_reviewed_lines=owner_lines,
            state=state,
        )
