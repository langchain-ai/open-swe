"""The Slack card of a standard human review request.

It names the pull request, says what it does in a line or two, and lists who has
signed up to review it on GitHub and where each of them stands.
"""

import json

from agent.human_review.requests import HumanReviewParticipant, HumanReviewRequest, slack_mention
from agent.slack.blocks import Block, ButtonElement, actions, button, context, escape, section
from agent.users import User

BUTTON_TYPE = "human_review"
UNCLAIMED_AFTER_MINUTES = 30
AUTO_MERGE_AFTER_HOURS = 2

_STATE_LABELS = {
    "APPROVED": ":white_check_mark: approved",
    "CHANGES_REQUESTED": ":warning: changes requested",
    "DISMISSED": ":eyes: reviewing",
}


def mention(user: User) -> str:
    return slack_mention(user, user.login_for("github") or "someone")


def _button_value(action: str, request: HumanReviewRequest) -> str:
    return json.dumps({"type": BUTTON_TYPE, "action": action, "fingerprint": str(request.id)})


def _label(request: HumanReviewRequest) -> str:
    pr = request.pull_request
    return f"{pr.owner}/{pr.repo}#{pr.number}"


def _reviewer_line(reviewer: HumanReviewParticipant, states: dict[str, str]) -> str:
    state = states.get(reviewer.github_login, "")
    status = _STATE_LABELS.get(state, ":eyes: reviewing")
    assigned = " _(picked by Open SWE)_" if reviewer.assigned_by_agent else ""
    return f"• {reviewer.slack_mention}{assigned} — {status}"


def _reviewers(request: HumanReviewRequest, states: dict[str, str]) -> list[Block]:
    reviewers = request.reviewers
    if not reviewers:
        return [
            section(
                "*Reviewers*\nNobody yet. If nobody signs up within "
                f"{UNCLAIMED_AFTER_MINUTES} minutes, Open SWE picks someone from the "
                "code's owners and review history."
            )
        ]
    lines = "\n".join(_reviewer_line(reviewer, states) for reviewer in reviewers)
    return [section(f"*Reviewers*\n{lines}")]


def _stats(request: HumanReviewRequest, author: str, requester: str | None) -> str:
    pr = request.pull_request
    parts = [f"By {author}"]
    if pr.additions is not None and pr.deletions is not None:
        files = f" in {pr.changed_files} files" if pr.changed_files else ""
        parts.append(f"+{pr.additions} −{pr.deletions}{files}")
    if pr.head_ref and pr.base_ref:
        parts.append(f"`{escape(pr.head_ref)}` → `{escape(pr.base_ref)}`")
    if requester is not None:
        parts.append(f"Requested by {requester}")
    return "  ·  ".join(parts)


def _buttons(request: HumanReviewRequest) -> list[ButtonElement]:
    return [
        button(
            "I'll review",
            action_id="open_swe_option_select_review",
            value=_button_value("review", request),
            style="primary",
        ),
        button(
            "Open on GitHub", action_id="open_swe_link_pull_request", url=request.pull_request.url
        ),
        button(
            "Dismiss",
            action_id="open_swe_option_select_dismiss",
            value=_button_value("dismiss", request),
        ),
    ]


def open_card(
    request: HumanReviewRequest,
    *,
    title: str,
    author: str,
    requester: str | None,
    review_states: dict[str, str],
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for an open request.

    ``author`` and ``requester`` are Slack mentions; ``review_states`` maps each
    GitHub login to its latest review state.
    """
    pr = request.pull_request
    blocks: list[Block] = [
        section(f":mag: *Review requested*  <{pr.url}|{_label(request)}>\n*{escape(title)}*"),
    ]
    if request.tldr:
        blocks.append(section("\n".join(f">{line}" for line in escape(request.tldr).splitlines())))
    blocks.append(context(_stats(request, author, requester)))
    blocks.extend(_reviewers(request, review_states))
    waiting = f"  Waiting on: {escape(request.detail)}." if request.detail else ""
    blocks.append(
        context(
            "Merges on its own once every reviewer approves, or "
            f"{AUTO_MERGE_AFTER_HOURS} hours after this request with at least one approval, "
            f"when checks pass and nothing is left unresolved.{waiting}"
        )
    )
    blocks.append(actions(*_buttons(request)))
    return f"Review requested for {_label(request)}: {title}", blocks


def closed_card(
    request: HumanReviewRequest, *, title: str, outcome: str
) -> tuple[str, list[Block]]:
    """A finished request collapses to one line; ``outcome`` is our own mrkdwn."""
    pr = request.pull_request
    return f"Review request: {outcome} — {pr.url}", [
        section(f"*Review request: {outcome}*\n<{pr.url}|{_label(request)}> {escape(title)}")
    ]
