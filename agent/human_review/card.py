"""The Slack card of a standard human review request.

It names the pull request, says what it does in a line or two, and lists who has
signed up to review it on GitHub and where each of them stands.
"""

import json

from agent.human_review.requests import HumanReviewParticipant, HumanReviewRequest, slack_mention
from agent.slack.blocks import Block, ButtonElement, actions, button, context, escape, section
from agent.users import User

BUTTON_TYPE = "human_review"

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
    blocks: list[Block] = []
    if reviewers := request.reviewers:
        lines = "\n".join(_reviewer_line(reviewer, states) for reviewer in reviewers)
        blocks.append(section(f"*Reviewers*\n{lines}"))
    if picks := request.picks:
        lines = "\n".join(
            f"• {pick.slack_mention} — :hourglass_flowing_sand: waiting for them to accept"
            for pick in picks
        )
        blocks.append(section(f"*Picked by Open SWE*\n{lines}"))
    return blocks


def _heading(request: HumanReviewRequest, states: dict[str, str]) -> str:
    link = f"<{request.pull_request.url}|{_label(request)}>"
    if "CHANGES_REQUESTED" in states.values() or "APPROVED" not in states.values():
        return f":mag: *Review requested*  {link}"
    return f":white_check_mark: *Approved by {_approvers(request, states)}*  {link}"


def _approvers(request: HumanReviewRequest, states: dict[str, str]) -> str:
    mentions = {r.github_login.lower(): r.slack_mention for r in request.reviewers}
    return ", ".join(
        mentions.get(login.lower(), f"@{login}")
        for login, state in states.items()
        if state == "APPROVED"
    )


def _stats(request: HumanReviewRequest, author: str, requester: str | None) -> str:
    pr = request.pull_request
    parts = [f"By {author}"]
    if pr.additions is not None and pr.deletions is not None:
        files = f" in {pr.changed_files} files" if pr.changed_files else ""
        parts.append(f"+{pr.additions} −{pr.deletions}{files}")
    if pr.head_ref and pr.base_ref:
        parts.append(f"`{escape(pr.head_ref)}` → `{escape(pr.base_ref)}`")
    if requester is not None and requester != author:
        parts.append(f"Requested by {requester}")
    return "  ·  ".join(parts)


def accept_button(request: HumanReviewRequest) -> ButtonElement:
    """What a picked reviewer clicks to take the review; anyone else clicking signs up instead."""
    return button(
        "Accept",
        action_id="open_swe_option_select_accept",
        value=_button_value("review", request),
        url=request.pull_request.url,
        style="primary",
    )


def _buttons(request: HumanReviewRequest) -> list[ButtonElement]:
    return [
        # Slack opens the URL and still delivers the click, so signing up lands on the PR.
        button(
            "I'll review",
            action_id="open_swe_option_select_review",
            value=_button_value("review", request),
            url=request.pull_request.url,
            style="primary",
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
    blocks: list[Block] = [section(f"{_heading(request, review_states)}\n*{escape(title)}*")]
    if request.tldr:
        blocks.append(section("\n".join(f">{line}" for line in escape(request.tldr).splitlines())))
    blocks.append(context(_stats(request, author, requester)))
    blocks.extend(_reviewers(request, review_states))
    if request.detail:
        blocks.append(context(f"Waiting on: {escape(request.detail)}."))
    blocks.append(actions(*_buttons(request)))
    return f"Review requested for {_label(request)}: {title}", blocks


def closed_card(
    request: HumanReviewRequest, *, title: str, outcome: str, review_states: dict[str, str]
) -> tuple[str, list[Block]]:
    """A finished request collapses to one line; ``outcome`` is our own mrkdwn."""
    pr = request.pull_request
    if outcome == "merged" and (approvers := _approvers(request, review_states)):
        outcome = f"merged — approved by {approvers}"
    return f"Review request: {outcome} — {pr.url}", [
        section(f"*Review request: {outcome}*\n<{pr.url}|{_label(request)}> {escape(title)}")
    ]
