"""Tool that starts an Open SWE review for a pull request."""

from fastapi import HTTPException

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.reviews import trigger_review_scout
from agent.run_config import RunConfig
from agent.slack.client import parse_github_pr_url


def _failure(error: str) -> dict[str, object]:
    return {"success": False, "error": error}


async def request_pr_review(
    pr_url: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
    pr_number: int | None = None,
) -> dict[str, object]:
    """Start an Open SWE review for a pull request."""
    if pr_url is not None:
        pr_ref = parse_github_pr_url(pr_url)
        if pr_ref is None:
            return _failure("pr_url must be a canonical GitHub pull request URL")
        owner, repo, pr_number = pr_ref.owner, pr_ref.repo, pr_ref.number
    elif not owner or not repo or pr_number is None:
        return _failure("Provide either pr_url or owner, repo, and pr_number")

    cfg = RunConfig.from_runtime()
    login = cfg.github_login
    if not login and cfg.user_email:
        from agent.users import User

        login = await User.login_for_email(cfg.user_email)
    if not login:
        return _failure(
            "GitHub login unavailable; link your GitHub account before starting a review"
        )

    assert owner is not None
    assert repo is not None
    assert pr_number is not None
    try:
        await require_repo_access_for_user(login, f"{owner}/{repo}")
        trigger = await trigger_review_scout(owner, repo, pr_number)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _failure(detail)
    except Exception as exc:
        return _failure(str(exc))

    return {
        "success": True,
        "started": trigger.started,
        "run_id": trigger.run_id,
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
    }
