"""What each GitHub delivery starts: the switchboard the backend's webhook was.

``route`` reads a delivery's event, action and payload, with what Open SWE knows
about its repository (``GitHubDeliveryFacts``), and names the backend's work it
starts, whether it may fire automations, and whether it starts an issue's run
here. It decides as the backend's ``/webhooks/github`` did. The backend keeps the
action sets and the mention handles too, so ``tests/agent/test_mda_github_routes.py``
holds these copies to them (AGENTS.md): standard library and ``runtime_api`` only,
so that test can load it.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final, cast

from runtime_api.github import GitHubDeliveryFacts, GitHubWorkName

CI_EVENTS: Final = frozenset({"check_run", "check_suite", "workflow_run", "status"})
ISSUE_ACTIONS: Final = frozenset({"opened", "edited", "reopened"})
COMMENT_ACTIONS: Final = {
    "issue_comment": frozenset({"created", "edited"}),
    "pull_request_review_comment": frozenset({"created", "edited"}),
    "pull_request_review": frozenset({"submitted", "edited"}),
}
PR_WATCH_TOGGLE_ACTIONS: Final = frozenset({"closed", "reopened", "converted_to_draft"})
PR_FIRST_REVIEW_ACTIONS: Final = frozenset({"opened", "ready_for_review"})
# The pull request actions that change the state the agent thread tracks.
PR_AGENT_STATE_ACTIONS: Final = frozenset(
    {"closed", "reopened", "converted_to_draft", "ready_for_review", "synchronize"}
)


@cache
def _mention(tags: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first, and not followed by more of a handle: deployments sharing a
    # workspace each own one, so @openswe must not match @openswe-preview.
    escaped = (re.escape(tag) for tag in sorted(tags, key=len, reverse=True))
    return re.compile("(?:" + "|".join(escaped) + r")(?![\w-])", re.IGNORECASE)


def mentions(text: str, tags: Sequence[str]) -> bool:
    """Whether ``text`` mentions one of Open SWE's handles."""
    return bool(text and tags) and _mention(tuple(tags)).search(text) is not None


@dataclass(frozen=True)
class Route:
    """What a delivery starts."""

    # The backend's work, in the order it runs.
    work: tuple[GitHubWorkName, ...] = ()
    # Whether the comment ``route_comment`` routes mentions Open SWE.
    mentioned: bool = False
    # The delivery may fire automations, whose runs start here, each on its own thread.
    automations: bool = False
    # An ``@openswe`` mention on an issue: its run starts on the issue's conversation.
    issue_run: bool = False


def _fields(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _pull_request(action: str, facts: GitHubDeliveryFacts, work: list[GitHubWorkName]) -> Route:
    if action in {"opened", "edited"} or action in PR_AGENT_STATE_ACTIONS:
        work.append("track_pull_request_state")
    if action == "synchronize":
        # Not gated on automatic review: an approval outlives turning review off.
        work += ["withdraw_superseded_approvals", "notify_pr_updated", "review_fork_push"]
    if action == "opened" or action in PR_AGENT_STATE_ACTIONS:
        work.append("track_pull_request_usage")
    if action == "closed":
        work.append("settle_review_on_close")
    elif action in PR_AGENT_STATE_ACTIONS:
        work.append("settle_human_reviews")
    if action in PR_WATCH_TOGGLE_ACTIONS:
        work.append("update_reviewer_watch")
    elif action in PR_FIRST_REVIEW_ACTIONS and facts.auto_review:
        work.append("review_pull_request")
    return Route(tuple(work), automations=action in {"opened", "closed"} and facts.allowed)


def route(
    event: str, payload: Mapping[str, object], facts: GitHubDeliveryFacts, *, from_app: bool
) -> Route:
    """What a delivery of ``event`` starts. ``from_app``: Open SWE's own App caused it."""
    if not facts.routable:
        return Route()
    if event == "merge_group":
        return Route(("wake_merge_queue",))
    action = str(payload.get("action") or "")
    work: list[GitHubWorkName] = []
    if event in {"push", "pull_request", "pull_request_review"}:
        work.append("wake_merge_queue")
    if event == "pull_request":
        return _pull_request(action, facts, work)
    if event == "push":
        if facts.auto_review:
            work.append("review_push")
        return Route(tuple(work))
    if not facts.allowed:
        return Route(tuple(work))
    if event in CI_EVENTS:
        return Route((*work, "ci_event"))
    if event == "pull_request_review" and action in {"submitted", "dismissed"}:
        # Any reviewer's verdict can complete a review request, Open SWE's own included.
        work.append("settle_human_reviews")
        if action == "submitted":
            work.append("record_completed_review")
    if event == "issues":
        if action not in ISSUE_ACTIONS:
            return Route(tuple(work))
        issue = _fields(payload.get("issue"))
        changes = _fields(payload.get("changes"))
        if action == "edited" and not any(field in changes for field in ("body", "title")):
            return Route(tuple(work))
        text = f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}"
        return Route(
            tuple(work),
            automations=action == "opened",
            issue_run=not from_app and mentions(text, facts.tags),
        )
    if action not in COMMENT_ACTIONS.get(event, frozenset()) or from_app:
        # The App's own comments and reviews start nothing, so the agent doesn't answer itself.
        return Route(tuple(work))
    comment = _fields(payload.get("comment") or payload.get("review"))
    body = str(comment.get("body") or "")
    mentioned = mentions(body, facts.tags)
    if event == "issue_comment" and not _fields(payload.get("issue")).get("pull_request"):
        return Route(tuple(work), issue_run=mentioned)
    if not mentioned and event == "issue_comment" and body.lstrip().startswith("!"):
        # An untagged workflow command, such as another bot's ``!merge``.
        return Route(tuple(work))
    return Route((*work, "route_comment"), mentioned=mentioned)
