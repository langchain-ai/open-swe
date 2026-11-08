"""Which agent runs a GitHub delivery starts in this deployment.

``route`` reads a delivery's event, action and payload, with what Open SWE knows
about its repository (``GitHubDeliveryFacts``), and says whether it may fire
automations, whether it is a pull request comment for the backend to route, and
whether it starts an issue's run here. The backend's own work for the delivery
(reviews, walkthroughs, human review, the merge queue, CI and the pull request
state the dashboard shows) is the backend's to pick and run when it records the
delivery (``openswe.github.work``). The backend keeps the action sets and the
mention handles too, so ``tests/agent/test_mda_github_routes.py`` holds these
copies to them (AGENTS.md): standard library and ``runtime_api`` only, so that
test can load it.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final, cast

from runtime_api.github import GitHubDeliveryFacts

ISSUE_ACTIONS: Final = frozenset({"opened", "edited", "reopened"})
COMMENT_ACTIONS: Final = {
    "issue_comment": frozenset({"created", "edited"}),
    "pull_request_review_comment": frozenset({"created", "edited"}),
    "pull_request_review": frozenset({"submitted", "edited"}),
}
# The pull request actions an automation's trigger fires on.
PR_AUTOMATION_ACTIONS: Final = frozenset({"opened", "closed"})
# The event keys whose deliveries may start an agent run: the channel's handler takes
# only these, and the backend gets every delivery forwarded either way.
AGENT_EVENT_KEYS: Final = frozenset(
    {f"issues.{action}" for action in ISSUE_ACTIONS}
    | {f"{event}.{action}" for event, actions in COMMENT_ACTIONS.items() for action in actions}
    | {f"pull_request.{action}" for action in PR_AUTOMATION_ACTIONS}
)


@cache
def _mention(tags: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first, and neither inside a longer word, an email or another handle nor
    # followed by more of one: deployments sharing a workspace each own one, so
    # @openswe must not match @openswe-preview, nor x@openswe.dev.
    escaped = (re.escape(tag) for tag in sorted(tags, key=len, reverse=True))
    return re.compile(r"(?<![\w.@-])(?:" + "|".join(escaped) + r")(?![\w-])", re.IGNORECASE)


def mentions(text: str, tags: Sequence[str]) -> bool:
    """Whether ``text`` mentions one of Open SWE's handles."""
    return bool(text and tags) and _mention(tuple(tags)).search(text) is not None


@dataclass(frozen=True)
class Route:
    """What a delivery starts in this deployment."""

    # A pull request comment, review or review comment for the backend to route.
    comment: bool = False
    # Whether that comment mentions Open SWE.
    mentioned: bool = False
    # The delivery may fire automations, whose runs start here, each on its own thread.
    automations: bool = False
    # An ``@openswe`` mention on an issue: its run starts on the issue's conversation.
    issue_run: bool = False


def _fields(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def route(
    event: str, payload: Mapping[str, object], facts: GitHubDeliveryFacts, *, from_app: bool
) -> Route:
    """What a delivery of ``event`` starts here. ``from_app``: Open SWE's own App caused it."""
    if not (facts.allowed and facts.routable):
        # Outside the repository allowlist, or no workspace owns the repository.
        return Route()
    action = str(payload.get("action") or "")
    if event == "pull_request":
        # A sender the public-repo gate turned away fires nothing.
        return Route(automations=action in PR_AUTOMATION_ACTIONS and not facts.gated)
    if event == "issues":
        if action not in ISSUE_ACTIONS:
            return Route()
        issue = _fields(payload.get("issue"))
        changes = _fields(payload.get("changes"))
        if action == "edited" and not any(field in changes for field in ("body", "title")):
            return Route()
        text = f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}"
        return Route(
            automations=action == "opened",
            issue_run=not from_app and mentions(text, facts.tags),
        )
    if action not in COMMENT_ACTIONS.get(event, frozenset()) or from_app:
        # The App's own comments and reviews start nothing, so the agent doesn't answer itself.
        return Route()
    comment = _fields(payload.get("comment") or payload.get("review"))
    body = str(comment.get("body") or "")
    mentioned = mentions(body, facts.tags)
    if event == "issue_comment" and not _fields(payload.get("issue")).get("pull_request"):
        return Route(issue_run=mentioned)
    if not mentioned and event == "issue_comment" and body.lstrip().startswith("!"):
        # An untagged workflow command, such as another bot's ``!merge``.
        return Route()
    return Route(comment=True, mentioned=mentioned)
