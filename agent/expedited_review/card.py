"""The Slack message people vote on: the whole diff plus Approve and Reject."""

import json

from agent.expedited_review.approvals import REQUIRED_APPROVALS, ExpeditedApproval
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
    modal,
    section,
    text_input,
)

BUTTON_TYPE = "expedited_review"
REJECT_MODAL_CALLBACK = "expedited_review_reject"
REJECT_FEEDBACK_BLOCK = "expedited_review_feedback"
REJECT_FEEDBACK_ACTION = "feedback"

_MAX_FILE_SECTIONS = 20


def _button_value(action: str, approval: ExpeditedApproval) -> str:
    return json.dumps({"type": BUTTON_TYPE, "action": action, "fingerprint": str(approval.id)})


def _vote_summary(approval: ExpeditedApproval) -> str:
    approvers = approval.approvers
    if not approvers:
        return (
            f"No approvals yet. {REQUIRED_APPROVALS} distinct reviewers with "
            "write access are needed."
        )
    names = ", ".join(f"@{login}" for login in approvers)
    remaining = max(REQUIRED_APPROVALS - len(approvers), 0)
    if remaining:
        return f"Approved by {names}. {remaining} more needed."
    return f"Approved by {names}."


def _header(approval: ExpeditedApproval, title: str) -> list[Block]:
    pr = approval.pull_request
    label = f"{pr.owner}/{pr.repo}#{pr.number}"
    return [
        section(f"*Expedited review requested*\n<{pr.url}|{label}> {escape(title)}"),
        context(f"Revision `{approval.head_sha[:12]}` · author @{escape(pr.author or 'unknown')}"),
    ]


def _diff_sections(files: list[ChangedFile]) -> list[Block]:
    sections: list[Block] = []
    for file in files[:_MAX_FILE_SECTIONS]:
        sections.append(section(f"`{escape(file.filename)}`  +{file.additions} −{file.deletions}"))
        sections.append(section(code_block(file.patch or "")))
    if len(files) > _MAX_FILE_SECTIONS:
        sections.append(context(f"{len(files) - _MAX_FILE_SECTIONS} more files on GitHub."))
    return sections


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


def open_card(
    approval: ExpeditedApproval, *, title: str, files: list[ChangedFile]
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for a card that is accepting votes."""
    pr = approval.pull_request
    blocks: list[Block] = [
        *_header(approval, title),
        divider(),
        *_diff_sections(files),
        divider(),
        section(_vote_summary(approval)),
        context(
            "Approve submits a GitHub review as you and, on the second approval, merges the "
            "pull request. The author may approve but that click does not become a GitHub review."
        ),
        actions(*_vote_buttons(approval)),
    ]
    text = f"Expedited review requested for {pr.url} ({approval.head_sha[:12]})"
    return text, blocks


def closed_card(
    approval: ExpeditedApproval, *, title: str, files: list[ChangedFile], outcome: str
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for a card whose vote is over."""
    pr = approval.pull_request
    blocks: list[Block] = [
        *_header(approval, title),
        divider(),
        *_diff_sections(files),
        divider(),
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
