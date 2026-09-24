"""The Slack message people vote on: the whole diff plus Approve and Dismiss."""

import json

from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.eligibility import ChangedFile
from agent.slack.blocks import (
    Block,
    ButtonElement,
    actions,
    button,
    code_block,
    context,
    divider,
    escape,
    image,
    section,
)

BUTTON_TYPE = "expedited_review"

_MAX_FILE_SECTIONS = 20
# Slack refuses a section over 3000 characters, and refusing means no card at all.
_MAX_PATCH_LINES = 60


def _button_value(action: str, approval: ExpeditedApproval) -> str:
    return json.dumps({"type": BUTTON_TYPE, "action": action, "fingerprint": str(approval.id)})


def _vote_summary(approval: ExpeditedApproval, author: str) -> str:
    """``author`` and the approvers are Slack mentions, so none of this is escaped."""
    if not approval.approvals:
        return f"Needs one approval from someone other than {author}."
    return f"Approved by {', '.join(vote.slack_mention for vote in approval.approvals)}."


def _header(approval: ExpeditedApproval, title: str, author: str) -> list[Block]:
    pr = approval.pull_request
    label = f"{pr.owner}/{pr.repo}#{pr.number}"
    return [
        section(f"*Expedited review requested*\n<{pr.url}|{label}> {escape(title)}"),
        context(f"Author {author}"),
    ]


def _diff_sections(files: list[ChangedFile], diff_image_id: str | None) -> list[Block]:
    shown, tests = ChangedFile.split(files)
    trailer = [*_overflow_note(shown), *_test_diffstat(tests)]
    if diff_image_id:
        names = ", ".join(escape(file.filename) for file in shown[:_MAX_FILE_SECTIONS])
        return [image(diff_image_id, f"Diff of {names}"), *trailer]
    sections: list[Block] = []
    for file in shown[:_MAX_FILE_SECTIONS]:
        sections.append(section(f"`{escape(file.filename)}`  +{file.additions} −{file.deletions}"))
        sections.append(section(code_block(_clip(file.patch or ""))))
    return [*sections, *trailer]


def _clip(patch: str) -> str:
    lines = patch.splitlines()
    if len(lines) <= _MAX_PATCH_LINES:
        return patch
    return "\n".join(
        [*lines[:_MAX_PATCH_LINES], f"… {len(lines) - _MAX_PATCH_LINES} more lines on GitHub"]
    )


def _overflow_note(files: list[ChangedFile]) -> list[Block]:
    if len(files) <= _MAX_FILE_SECTIONS:
        return []
    return [context(f"{len(files) - _MAX_FILE_SECTIONS} more files on GitHub.")]


def _test_diffstat(tests: list[ChangedFile]) -> list[Block]:
    """Test files are never drawn; the card lists them with their line counts instead."""
    if not tests:
        return []
    lines = [
        f"`{escape(file.filename)}`  +{file.additions} −{file.deletions}"
        for file in tests[:_MAX_FILE_SECTIONS]
    ]
    if len(tests) > _MAX_FILE_SECTIONS:
        lines.append(f"{len(tests) - _MAX_FILE_SECTIONS} more test files on GitHub.")
    return [context("*Tests (not shown)*\n" + "\n".join(lines))]


def _vote_buttons(approval: ExpeditedApproval) -> tuple[ButtonElement, ...]:
    return (
        button(
            "Approve",
            action_id="open_swe_option_select_approve",
            value=_button_value("approve", approval),
            style="primary",
        ),
        _dismiss_button(approval),
    )


def _ready_button(approval: ExpeditedApproval) -> ButtonElement:
    return button(
        "Mark ready for review",
        action_id="open_swe_option_select_ready",
        value=_button_value("ready", approval),
        style="primary",
    )


def _dismiss_button(approval: ExpeditedApproval) -> ButtonElement:
    return button(
        "Dismiss",
        action_id="open_swe_option_select_dismiss",
        value=_button_value("dismiss", approval),
    )


def _voting_diff(
    approval: ExpeditedApproval, files: list[ChangedFile], diff_image_id: str | None
) -> list[Block]:
    """The diff voters read; an approved card no longer needs it."""
    if approval.approved:
        return []
    return [*_diff_sections(files, diff_image_id), divider()]


def _status(approval: ExpeditedApproval, author: str) -> list[Block]:
    if approval.awaiting_ready:
        return [
            section(f"*Draft.* {author}, mark it ready for review so someone else can approve it."),
            actions(_ready_button(approval), _dismiss_button(approval)),
        ]
    if approval.approved:
        return [
            section(
                f"*{_vote_summary(approval, author)}* Merging once checks and reviews are clean."
            )
        ]
    return [section(_vote_summary(approval, author)), actions(*_vote_buttons(approval))]


def open_card(
    approval: ExpeditedApproval,
    *,
    title: str,
    author: str,
    files: list[ChangedFile],
    diff_image_id: str | None = None,
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for an open card; diff and buttons go once it is approved.

    ``author`` is the PR author's Slack mention, from :meth:`ExpeditedApproval.author_mention`.
    """
    pr = approval.pull_request
    blocks: list[Block] = [
        *_header(approval, title, author),
        divider(),
        *_voting_diff(approval, files, diff_image_id),
        *_status(approval, author),
    ]
    text = f"Expedited review requested for {pr.url}"
    return text, blocks


def closed_card(
    approval: ExpeditedApproval,
    *,
    title: str,
    author: str,
    files: list[ChangedFile],
    outcome: str,
    diff_image_id: str | None = None,
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for a card whose vote is over; ``outcome`` is our own mrkdwn.

    A merged or cancelled card collapses to one line, since nothing is left to read or click.
    """
    pr = approval.pull_request
    if approval.state in {"merged", "cancelled"}:
        label = f"{pr.owner}/{pr.repo}#{pr.number}"
        return f"Expedited review: {outcome} — {pr.url}", [
            section(f"*Expedited review: {outcome}*\n<{pr.url}|{label}> {escape(title)}")
        ]
    blocks: list[Block] = [
        *_header(approval, title, author),
        divider(),
        *_voting_diff(approval, files, diff_image_id),
        section(f"*{outcome}*"),
        context(_vote_summary(approval, author)),
    ]
    return f"{outcome} — {pr.url}", blocks
