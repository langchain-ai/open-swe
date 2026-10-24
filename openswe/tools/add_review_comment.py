"""Tool: ``add_review_comment``. Adds a line comment to the user's pending GitHub review."""

from fastapi import HTTPException
from pydantic import BaseModel

from openswe.audit_logs.tools import audit_tool
from openswe.credential_scope import pr_author_login
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient, GitHubError, GitHubSignInRequired, GraphQLError
from openswe.review.reviews import PendingReview, PendingReviewCommentInput
from openswe.run_config import RunConfig
from openswe.tools.diff_lines import DiffRange, DiffSide, comment_text


class AddReviewCommentResult(BaseModel):
    added: bool
    range: DiffRange | None = None
    body: str | None = None
    review_id: int | None = None
    login: str | None = None
    note: str | None = None
    error: str | None = None


@audit_tool()
async def add_review_comment(
    file: str,
    line: int,
    body: str,
    start_line: int | None = None,
    side: DiffSide = "RIGHT",
) -> dict[str, object]:
    """Implement the `add_review_comment` tool."""
    return (await _add(file, line, body, start_line, side)).model_dump(
        mode="json", exclude_none=True
    )


async def _add(
    file: str, line: int, body: str, start_line: int | None, side: DiffSide
) -> AddReviewCommentResult:
    try:
        lines = DiffRange.of(file, line, start_line, side)
        text = comment_text(body)
    except ValueError as exc:
        return AddReviewCommentResult(added=False, error=str(exc))
    cfg = RunConfig.from_runtime()
    if not cfg.repo or cfg.pr_number is None:
        return AddReviewCommentResult(
            added=False, error="only a conversation about a pull request can comment on it"
        )
    # Only a message the person sent from this PR's review page, where the
    # comment appears on its line at once; runs from anywhere else could be
    # steered into it unseen. They can still be drafted with `propose_review_comment`.
    page = f"https://github.com/{cfg.repo.full_name}/pull/{cfg.pr_number}"
    if (cfg.review_page_pr_url or "").rstrip("/").lower() != page.lower():
        return AddReviewCommentResult(
            added=False,
            error=(
                "only works on a message sent from this pull request's review page;"
                " use propose_review_comment instead"
            ),
        )
    try:
        login = await pr_author_login()
    except RuntimeError as exc:
        return AddReviewCommentResult(added=False, error=str(exc))
    if login is None:
        return AddReviewCommentResult(
            added=False, error="this conversation has no person to comment as"
        )
    comment = PendingReviewCommentInput(
        path=lines.file,
        line=lines.end_line,
        side=lines.side,
        start_line=lines.start_line,
        body=text,
    )
    try:
        await require_repo_access_for_user(login, cfg.repo.full_name)
        async with GitHubClient.as_user(login) as github:
            pull = github.repo(cfg.repo.owner, cfg.repo.name).pull_request(cfg.pr_number)
            review = await PendingReview.add_comment(pull, comment, login=login)
    except HTTPException as exc:
        return AddReviewCommentResult(added=False, error=str(exc.detail))
    except GitHubSignInRequired:
        return AddReviewCommentResult(
            added=False, error=f"{login} must sign in to Open SWE with GitHub again"
        )
    except (GitHubError, GraphQLError) as exc:
        return AddReviewCommentResult(added=False, error=exc.message)
    return AddReviewCommentResult(
        added=True,
        range=lines,
        body=text,
        review_id=review.id,
        login=login,
        note=(
            f"In {login}'s pending GitHub review, which only they see until they submit it;"
            " every pending comment posts with the review."
        ),
    )
