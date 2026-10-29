"""Reviewer and review-scout runs that ended in failure, made visible to their audience.

Neither kind of run has a person or channel to tell, so the run-complete
webhook's failure reply has nowhere to go. A reviewer's audience reads the pull
request: its check is settled and its status comment says the review couldn't
run. A scout's audience reads the review page, which shows the failed run
itself. Both are logged so failures can be counted and alerted on.
"""

import logging
import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from openswe.github.app import get_github_app_installation_token
from openswe.github.checks import CheckConclusion
from openswe.github.http import GitHubError
from openswe.github.pull_request_status import PullRequestClient
from openswe.review.findings import REVIEWER_THREAD_KIND, set_reviewer_thread_metadata
from openswe.review.publish import render_failed_status_comment, settle_review_check_run

logger = logging.getLogger(__name__)

ReviewRunKind = Literal["reviewer", "review-scout"]
_REVIEW_RUN_KINDS: frozenset[str] = frozenset({REVIEWER_THREAD_KIND, "review-scout"})
# ``BackendCallError`` messages name the endpoint first: "prepare failed (ReadTimeout)".
_BACKEND_ERRORS = frozenset({"BackendCallError", "BackendTimeoutError", "BackendUnavailableError"})
_PHASE = re.compile(r"^(\w+) failed\b")
_NOT_FOUND = 404


class _CheckResult(BaseModel):
    conclusion: CheckConclusion
    title: str = "Review completed"
    summary: str = ""


class _PullRequestRef(BaseModel):
    owner: str = ""
    name: str = ""
    number: int | None = None


class _ReviewThread(BaseModel):
    """The fields of a reviewer or scout thread's metadata a failed run is reported from."""

    model_config = ConfigDict(extra="ignore")

    kind: str = ""
    pr: _PullRequestRef = _PullRequestRef()
    head_sha: str = ""
    last_reviewed_sha: str = ""
    current_reviewer_run_id: str | None = None
    review_check_run_id: int | None = None
    review_check_pending_result: _CheckResult | None = None
    status_comment_id: int | None = None


class _TerminalError(BaseModel):
    error: str | None = None
    message: str | None = None

    @property
    def phase(self) -> str | None:
        if self.error not in _BACKEND_ERRORS or not self.message:
            return None
        match = _PHASE.match(self.message)
        return match.group(1) if match else None


class ReviewRunFailure(BaseModel):
    """A reviewer or review-scout run that ended in ``error`` or ``timeout``."""

    run_kind: ReviewRunKind
    thread_id: str
    run_id: str | None
    status: str
    code: str | None
    phase: str | None
    reason: str
    thread: _ReviewThread

    @classmethod
    def of(
        cls,
        *,
        thread_id: str,
        run_id: str | None,
        status: str,
        error: object,
        code: str | None,
        reason: str,
        metadata: Mapping[str, object],
    ) -> Self | None:
        """The failure, or ``None`` for a thread that is not a reviewer's or a scout's."""
        if metadata.get("kind") not in _REVIEW_RUN_KINDS:
            return None
        try:
            thread = _ReviewThread.model_validate(metadata)
        except ValidationError:
            logger.warning(
                "Review thread has malformed metadata",
                exc_info=True,
                extra={"thread_id": thread_id},
            )
            thread = _ReviewThread(kind=str(metadata.get("kind")))
        try:
            terminal = _TerminalError.model_validate(error) if isinstance(error, dict) else None
        except ValidationError:
            logger.warning("Unparseable run error", exc_info=True, extra={"thread_id": thread_id})
            terminal = None
        return cls(
            run_kind="review-scout" if thread.kind == "review-scout" else "reviewer",
            thread_id=thread_id,
            run_id=run_id,
            status=status,
            code=code,
            phase=terminal.phase if terminal else None,
            reason=reason,
            thread=thread,
        )

    @property
    def log_extra(self) -> dict[str, object]:
        pr = self.thread.pr
        return {
            "review_run_failure": {
                "run_kind": self.run_kind,
                "phase": self.phase,
                "code": self.code,
                "status": self.status,
                "pr_repo_full_name": f"{pr.owner}/{pr.name}" if pr.owner and pr.name else None,
                "pr_number": pr.number,
                "thread_id": self.thread_id,
                "run_id": self.run_id,
            }
        }

    def log(self) -> None:
        logger.error("Review run failed", extra=self.log_extra)

    async def report(self) -> bool:
        """Leave the failure where the run's audience looks; whether anything was posted.

        A scout's failed run is what the review page reads, so it needs nothing more.
        """
        if self.run_kind != REVIEWER_THREAD_KIND:
            return False
        pr = self.thread.pr
        if not pr.owner or not pr.name:
            return False
        if self.thread.current_reviewer_run_id not in {None, self.run_id}:
            # A newer run owns the check and the status comment now.
            return False
        pending = self.thread.review_check_pending_result
        try:
            await self._settle_check(pending)
        except Exception:
            logger.warning(
                "Could not settle the failed review's check", exc_info=True, extra=self.log_extra
            )
        if pending is not None or not self._unpublished:
            return False
        try:
            return await self._post_status_comment()
        except Exception:
            logger.warning(
                "Could not post the failed review's status comment",
                exc_info=True,
                extra=self.log_extra,
            )
            return False

    @property
    def _unpublished(self) -> bool:
        """Whether the run died before publishing: publish settles the check and clears the comment."""
        thread = self.thread
        return (
            thread.review_check_run_id is not None
            or thread.status_comment_id is not None
            or thread.last_reviewed_sha != thread.head_sha
        )

    async def _settle_check(self, pending: _CheckResult | None) -> None:
        if self.thread.review_check_run_id is None:
            return
        token = await get_github_app_installation_token()
        if not token:
            logger.warning(
                "No GitHub token to settle the failed review's check", extra=self.log_extra
            )
            return
        # Neutral, not failure: a red X misreads an Open SWE outage as a problem in the code.
        result = pending or _CheckResult(
            conclusion="neutral",
            title="Review couldn't run",
            summary=(
                f"Open SWE stopped before it could review this pull request: {self.reason}. "
                "Push a commit or re-request a review from Open SWE to run it again."
            ),
        )
        await settle_review_check_run(
            thread_id=self.thread_id,
            owner=self.thread.pr.owner,
            repo=self.thread.pr.name,
            token=token,
            conclusion=result.conclusion,
            title=result.title,
            summary=result.summary,
        )

    async def _post_status_comment(self) -> bool:
        """Turn the PR's status comment into the failure notice, posting one if there is none.

        Edited in place, so reporting the same run twice leaves one comment.
        """
        pr = self.thread.pr
        if pr.number is None:
            return False
        body = render_failed_status_comment(
            pr_number=pr.number, thread_id=self.thread_id, reason=self.reason
        )
        async with PullRequestClient.as_app(pr.owner, pr.name, pr.number) as pull:
            if (comment_id := self.thread.status_comment_id) is not None:
                try:
                    await pull.edit_comment(comment_id, body)
                except GitHubError as exc:
                    if exc.response.status_code != _NOT_FOUND:
                        raise
                else:
                    return True
            posted = await pull.comment(body)
        new_id = posted.get("id") if isinstance(posted, dict) else None
        if not isinstance(new_id, int):
            raise ValueError("GitHub answered the comment without an id")
        await set_reviewer_thread_metadata(self.thread_id, extra={"status_comment_id": new_id})
        return True
