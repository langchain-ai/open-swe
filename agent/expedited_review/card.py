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


def _failing_check_warning(failing_checks: list[str]) -> list[Block]:
    if not failing_checks:
        return []
    noun = "check" if len(failing_checks) == 1 else "checks"
    names = ", ".join(escape(name) for name in failing_checks)
    return [
        section(
            f":warning: *GitHub does not require the failing {noun}, so this can still "
            f"merge:* {names}"
        )
    ]


def open_card(
    approval: ExpeditedApproval,
    *,
    title: str,
    files: list[ChangedFile],
    failing_checks: list[str] | None = None,
    diff_image_id: str | None = None,
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for a card that is accepting votes."""
    pr = approval.pull_request
    blocks: list[Block] = [
        *_header(approval, title),
        divider(),
        *_diff_sections(files, diff_image_id),
        divider(),
        section(_vote_summary(approval)),
        *_failing_check_warning(failing_checks or []),
        context(
            "Approve submits a GitHub review as you and, on the second approval, merges the "
            "pull request. The author may approve but that click does not become a GitHub review."
        ),
        actions(*_vote_buttons(approval)),
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
    blocks: list[Block] = [
        *_header(approval, title),
        divider(),
        *_diff_sections(files, diff_image_id),
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
