"""Point a new message that mentions a pull request at the threads already working on it."""

import json
import logging
from collections.abc import Mapping
from typing import Any

from openswe.input_messages import RunMessage, SystemIdentity, build_input_messages
from openswe.prompts import prompt
from openswe.slack.client import parse_github_pr_url
from openswe.slack.pr_links import linked_pull_request_urls
from openswe.threads.listing import list_dashboard_threads_page

logger = logging.getLogger(__name__)

PR_HANDOFF_SYSTEM: SystemIdentity = {
    "id": "system:pr-thread-handoff",
    "display_name": "Pull request thread handoff",
    "platform": "open-swe",
}
_PAGE_SIZE = 25


def summary_matches_pr(summary: Mapping[str, Any], locator: str) -> bool:
    """Whether a thread summary tracks the pull request at ``locator``."""
    expected = parse_github_pr_url(locator)
    if expected is None:
        return False
    pr = summary.get("pr")
    pull_requests = summary.get("pullRequests")
    records = [pr] if isinstance(pr, Mapping) else []
    if isinstance(pull_requests, list):
        records.extend(record for record in pull_requests if isinstance(record, Mapping))
    for record in records:
        url = record.get("url")
        actual = parse_github_pr_url(url) if isinstance(url, str) else None
        if (
            actual is not None
            and actual.owner.lower() == expected.owner.lower()
            and actual.repo.lower() == expected.repo.lower()
            and actual.number == expected.number
        ):
            return True
    return False


async def _linked_threads(
    url: str, *, current_thread_id: str, login: str, email: str | None
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    offset = 0
    while True:
        page = await list_dashboard_threads_page(
            login,
            email=email,
            limit=_PAGE_SIZE,
            offset=offset,
            query=url,
            surfaced_only=True,
            include_private=False,
        )
        for item in page.get("items", []):
            if not isinstance(item, Mapping) or not summary_matches_pr(item, url):
                continue
            thread_id = item.get("id")
            if not isinstance(thread_id, str) or thread_id == current_thread_id:
                continue
            target = {"pr_url": url, "thread_id": thread_id}
            for key in ("title", "webUrl", "slackUrl"):
                value = item.get(key)
                if isinstance(value, str):
                    target[key] = value
            targets.append(target)
        if not page.get("hasMore"):
            return targets
        offset += _PAGE_SIZE


async def pr_handoff_message(
    content: object, *, current_thread_id: str, login: str | None, email: str | None
) -> RunMessage | None:
    """A system message naming the other threads tied to PRs ``content`` links, if any."""
    urls = linked_pull_request_urls(content)
    if not urls or not login:
        return None
    targets: list[dict[str, str]] = []
    try:
        for url in sorted(urls):
            targets.extend(
                await _linked_threads(
                    url, current_thread_id=current_thread_id, login=login, email=email
                )
            )
    except Exception:
        logger.warning(
            "Pull request thread handoff lookup failed",
            extra={"agent_thread_id": current_thread_id},
            exc_info=True,
        )
        return None
    if not targets:
        return None
    return build_input_messages(
        prompt("runs/pr-thread-reminder", targets=json.dumps(targets, ensure_ascii=True)),
        {"sender_id": PR_HANDOFF_SYSTEM["id"], "surface": "automation", "kind": "system"},
        injected_dynamic_context_hashes={PR_HANDOFF_SYSTEM["id"]},
    )[0]
