"""The Slack message people vote on: the whole diff plus Approve and Reject."""

import json

from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.eligibility import ChangedFile
from agent.slack.blocks import (
    Block,
    ButtonElement,
    ModalView,
    actions,
    button,
    code_block,
    context,
    divider,
    escape,
    image,
    modal,
    section,
    text_input,
)

BUTTON_TYPE = "expedited_review"
REJECT_MODAL_CALLBACK = "expedited_review_reject"
REJECT_FEEDBACK_BLOCK = "expedited_review_feedback"
REJECT_FEEDBACK_ACTION = "feedback"

_MAX_FILE_SECTIONS = 20
# Slack refuses a section over 3000 characters, and refusing means no card at
# all. Only a shown test diff ever reaches this; source diffs cap out at 20 lines.
_MAX_PATCH_LINES = 60


def _button_value(action: str, approval: ExpeditedApproval) -> str:
    return json.dumps({"type": BUTTON_TYPE, "action": action, "fingerprint": str(approval.id)})


def _author(approval: ExpeditedApproval) -> str:
    return f"@{escape(approval.pull_request.author or 'the author')}"


def _vote_summary(approval: ExpeditedApproval) -> str:
    if not approval.approvers:
        return f"Needs one approval from someone other than {_author(approval)}."
    return f"Approved by {', '.join(f'@{login}' for login in approval.approvers)}."


def _header(approval: ExpeditedApproval, title: str) -> list[Block]:
    pr = approval.pull_request
    label = f"{pr.owner}/{pr.repo}#{pr.number}"
    return [
        section(f"*Expedited review requested*\n<{pr.url}|{label}> {escape(title)}"),
        context(f"Revision `{approval.head_sha[:12]}` · author @{escape(pr.author or 'unknown')}"),
    ]


def _diff_sections(files: list[ChangedFile], diff_image_id: str | None) -> list[Block]:
    shown, named = ChangedFile.rendered(files)
    trailer = [*_overflow_note(shown), *_test_note(named)]
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


def _test_note(tests: list[ChangedFile]) -> list[Block]:
    """Names the test files the card is not drawing."""
    if not tests:
        return []
    noun = "test file" if len(tests) == 1 else "test files"
    return [
        context(f"{ChangedFile.total_lines(tests)} more lines in {len(tests)} {noun}, on GitHub.")
    ]


def _vote_buttons(approval: ExpeditedApproval) -> tuple[ButtonElement, ButtonElement]:
    return (
        button(
            "Approve",
            action_id="open_swe_option_select_approve",
            value=_button_value("approve", approval),
            style="primary",
        ),
        button(
            "Reject and give feedback",
            action_id="open_swe_option_select_reject",
            value=_button_value("reject", approval),
            style="danger",
        ),
    )


def _ready_button(approval: ExpeditedApproval) -> ButtonElement:
    return button(
        "Mark ready for review",
        action_id="open_swe_option_select_ready",
        value=_button_value("ready", approval),
        style="primary",
    )


def _voting_diff(
    approval: ExpeditedApproval, files: list[ChangedFile], diff_image_id: str | None
) -> list[Block]:
    """The diff voters read; an approved card no longer needs it."""
    if approval.approved:
        return []
    return [*_diff_sections(files, diff_image_id), divider()]


def _status(approval: ExpeditedApproval) -> list[Block]:
    if approval.awaiting_ready:
        return [
            section(
                f"*Draft.* {_author(approval)}, mark it ready for review so someone else "
                "can approve it."
            ),
            actions(_ready_button(approval)),
        ]
    if approval.approved:
        return [
            section(
                f"*{escape(_vote_summary(approval))}* Merging once checks and reviews are clean."
            )
        ]
    return [section(_vote_summary(approval)), actions(*_vote_buttons(approval))]


def open_card(
    approval: ExpeditedApproval,
    *,
    title: str,
    files: list[ChangedFile],
    diff_image_id: str | None = None,
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for an open card; diff and buttons go once it is approved."""
    pr = approval.pull_request
    blocks: list[Block] = [
        *_header(approval, title),
        divider(),
        *_voting_diff(approval, files, diff_image_id),
        *_status(approval),
    ]
    text = f"Expedited review requested for {pr.url} ({approval.head_sha[:12]})"
    return text, blocks


def closed_card(
    approval: ExpeditedApproval,
    *,
    title: str,
    files: list[ChangedFile],
    outcome: str,
    diff_image_id: str | None = None,
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for a card whose vote is over."""
    pr = approval.pull_request
    if approval.state == "merged":
        label = f"{pr.owner}/{pr.repo}#{pr.number}"
        return f"Expedited review: merged — {pr.url}", [
            section(f"*Expedited review: merged*\n<{pr.url}|{label}> {escape(title)}")
        ]
    blocks: list[Block] = [
        *_header(approval, title),
        divider(),
        *_voting_diff(approval, files, diff_image_id),
        section(f"*{escape(outcome)}*"),
        context(_vote_summary(approval)),
    ]
    return f"{outcome} — {pr.url}", blocks


def reject_modal(approval: ExpeditedApproval, *, channel_id: str, thread_ts: str) -> ModalView:
    pr = approval.pull_request
    return modal(
        callback_id=REJECT_MODAL_CALLBACK,
        title="Reject expedited review",
        submit="Reject",
        close="Cancel",
        private_metadata=json.dumps(
            {"approval_id": str(approval.id), "channel_id": channel_id, "thread_ts": thread_ts}
        ),
        blocks=[
            section(f"Rejecting <{pr.url}|{pr.owner}/{pr.repo}#{pr.number}> ends this vote."),
            text_input(
                block_id=REJECT_FEEDBACK_BLOCK,
                label="Feedback for the agent",
                action_id=REJECT_FEEDBACK_ACTION,
                multiline=True,
                max_length=3000,
                placeholder="What should change?",
                optional=True,
            ),
        ],
    )
