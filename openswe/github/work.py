"""Which of the backend's own jobs a GitHub delivery starts.

MDA's GitHub channel hands every delivery to the backend (``/mda/github/deliveries``),
and the backend picks its work here, from the delivery's event, its action and what
Open SWE knows about its repository: reviews, walkthroughs, human review, the merge
queue, CI and the pull request state the dashboard shows. The channel decides only
what starts an agent run there (``mda/rt/github_routes.py``).
"""

from collections.abc import Mapping
from typing import Final

from runtime_api.github import GitHubDeliveryFacts, GitHubWorkName

from openswe.webhooks.common import (
    GH_PR_AGENT_STATE_ACTIONS,
    GH_PR_FIRST_REVIEW_ACTIONS,
    GH_PR_WATCH_TOGGLE_ACTIONS,
    GITHUB_CI_EVENTS,
)

# GitHub sends these to every App: it was installed, removed or suspended, or its
# repositories changed.
INSTALLATION_EVENTS: Final = frozenset({"installation", "installation_repositories"})
# Pull request actions after which the walkthrough plans the pull request, drafts included.
PR_WALKTHROUGH_ACTIONS: Final = frozenset({"opened", "reopened", "ready_for_review", "synchronize"})
# Pull request actions that can change who may approve it, or the base whose rules apply.
APPROVAL_POLICY_ACTIONS: Final = frozenset(
    {"opened", "reopened", "synchronize", "ready_for_review", "edited"}
)
REVIEW_THREAD_ACTIONS: Final = frozenset({"resolved", "unresolved"})


def delivery_work(
    event: str, payload: Mapping[str, object], facts: GitHubDeliveryFacts
) -> tuple[GitHubWorkName, ...]:
    """The backend's work a delivery of ``event`` starts, in the order it runs."""
    if not facts.allowed:
        # The repository allowlist doesn't take it.
        return ()
    if event in INSTALLATION_EVENTS:
        # About the installation, not a repository, so no workspace owns it.
        return ("forget_cached_repos",)
    if not facts.routable:
        return ()
    if event == "merge_group":
        return ("wake_merge_queue",)
    action = str(payload.get("action") or "")
    work: list[GitHubWorkName] = []
    if event in {"push", "pull_request", "pull_request_review"}:
        work.append("wake_merge_queue")
    if event == "pull_request":
        return (*work, *_pull_request(action, facts))
    if event == "push":
        if facts.auto_review:
            work.append("review_push")
    elif event in GITHUB_CI_EVENTS:
        work.append("ci_event")
        if event == "check_run" and action == "completed":
            # It may say a pull request's preview is deployed (``PreviewReady``).
            work.append("review_apps_on_preview")
    elif event == "pull_request_review_thread":
        if action in REVIEW_THREAD_ACTIONS:
            work.append("sync_review_thread")
    elif event == "pull_request_review" and action in {"submitted", "dismissed"}:
        # Any reviewer's verdict can complete a review request, Open SWE's own included.
        work.append("settle_human_reviews")
        if action == "submitted":
            work.append("record_completed_review")
        work.append("evaluate_approval_policy")
    return tuple(work)


def _pull_request(action: str, facts: GitHubDeliveryFacts) -> list[GitHubWorkName]:
    if facts.gated:
        # The public-repo gate turned the sender away.
        return []
    work: list[GitHubWorkName] = []
    if action in PR_WALKTHROUGH_ACTIONS:
        work.append("walkthrough_pull_request")
    if action in {"opened", "edited"} or action in GH_PR_AGENT_STATE_ACTIONS:
        work.append("track_pull_request_state")
    if action == "synchronize":
        # Not gated on automatic review: an approval outlives turning review off.
        work += ["withdraw_superseded_approvals", "review_fork_push"]
    if action == "opened" or action in GH_PR_AGENT_STATE_ACTIONS:
        work.append("track_pull_request_usage")
    if action in APPROVAL_POLICY_ACTIONS:
        work.append("evaluate_approval_policy")
    if action == "closed":
        work += [
            "settle_review_on_close",
            "stop_preview_runs_on_close",
            "delete_app_documents_on_close",
        ]
    elif action in GH_PR_AGENT_STATE_ACTIONS:
        work.append("settle_human_reviews")
    if action in GH_PR_WATCH_TOGGLE_ACTIONS:
        work.append("update_reviewer_watch")
    elif action in GH_PR_FIRST_REVIEW_ACTIONS and facts.auto_review:
        work.append("review_pull_request")
    return work
