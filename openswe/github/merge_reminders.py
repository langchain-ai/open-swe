"""Opt-in daily Slack reminders for approved pull requests."""

import logging
from datetime import UTC, datetime
from html import escape

from langgraph_sdk import get_client

from openswe.dashboard.profiles import get_valid_access_token
from openswe.expedited_review.readiness import Readiness
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import (
    OpenPullRequest,
    PullRequestClient,
    list_open_pull_requests,
)
from openswe.slack.dm import send_dm
from openswe.store import get_value, put_value
from openswe.users import User

logger = logging.getLogger(__name__)
TASK = "approved_pr_dm_reminders"


def qualifies(pr: OpenPullRequest) -> bool:
    return (
        pr.status_available
        and pr.draft is False
        and pr.mergeable is True
        and pr.review_decision == "approved"
        and pr.ci in {"passing", "none"}
        and not pr.missing_checks
        and (
            pr.merge_state in {"clean", "unstable", "has_hooks"}
            or (pr.merge_state == "blocked" and bool(pr.unresolved_threads))
        )
    )


async def ensure_reminder_cron(login: str) -> None:
    client = get_client()
    metadata = {"kind": TASK, "login": login}
    if await client.crons.search(metadata=metadata, limit=1):
        return
    await client.crons.create(
        "scheduler",
        schedule="0 * * * *",
        input={"task": TASK, "login": login},
        metadata=metadata,
        timezone="UTC",
    )


async def run_reminders(login: str) -> dict[str, int]:
    user = await User.for_login("github", login)
    if (
        user is None
        or not user.typed_preferences.approved_pr_dm_reminders
        or not user.slack_user_id
    ):
        return {"sent": 0}
    token = await get_valid_access_token(login)
    if token is None:
        return {"sent": 0}
    namespace = (TASK, str(user.id))
    today = datetime.now(UTC).date().isoformat()
    sent = 0
    page: int | None = 1
    async with GitHubClient.connect(token=token) as github:
        while page is not None:
            results = await list_open_pull_requests(github, login, page=page)
            if results.incomplete:
                logger.warning("Incomplete PR search for merge reminders", extra={"login": login})
                break
            for pr in results.pull_requests:
                key = f"{pr.repo}#{pr.number}"
                if not qualifies(pr) or await get_value(namespace, key) == {"date": today}:
                    continue
                owner, repo = pr.repo.split("/", 1)
                readiness = await Readiness.assess(
                    PullRequestClient(github.repo(owner, repo), pr.number)
                )
                if readiness is None or readiness.snapshot.head_sha != pr.head_sha:
                    continue
                if any("unresolved review" not in blocker for blocker in readiness.blockers):
                    continue
                url = f"https://github.com/{pr.repo}/pull/{pr.number}"
                suffix = (
                    f" — {pr.unresolved_threads} unresolved review thread(s) to address."
                    if pr.unresolved_threads
                    else " — ready to merge."
                )
                if await send_dm(
                    user.slack_user_id,
                    f"Approved: <{url}|{escape(pr.title, quote=False)}> ({pr.repo}){suffix}",
                ):
                    await put_value(namespace, key, {"date": today})
                    sent += 1
            page = results.next_page
    return {"sent": sent}
