"""The Slack message people vote on: the whole diff plus Approve and Reject."""

import json
from typing import Any

from agent.expedited_review.approvals import REQUIRED_APPROVALS, ExpeditedApproval
from agent.expedited_review.eligibility import ChangedFile

BUTTON_TYPE = "expedited_review"
REJECT_MODAL_CALLBACK = "expedited_review_reject"
REJECT_FEEDBACK_BLOCK = "expedited_review_feedback"
REJECT_FEEDBACK_ACTION = "feedback"

_SECTION_LIMIT = 2900
_MAX_FILE_SECTIONS = 20


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _code_block(patch: str) -> str:
    body = _escape(patch).replace("```", "` ` `")
    budget = _SECTION_LIMIT - 20
    if len(body) > budget:
        body = body[:budget].rstrip() + "\n…"
    return f"```\n{body}\n```"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def _button_value(action: str, approval: ExpeditedApproval) -> str:
    return json.dumps({"type": BUTTON_TYPE, "action": action, "fingerprint": str(approval.id)})


def _vote_summary(approval: ExpeditedApproval) -> str:
    approvers = approval.approvers
    if not approvers:
        return f"No approvals yet. {REQUIRED_APPROVALS} distinct reviewers with write access are needed."
    names = ", ".join(f"@{login}" for login in approvers)
    remaining = max(REQUIRED_APPROVALS - len(approvers), 0)
    if remaining:
        return f"Approved by {names}. {remaining} more needed."
    return f"Approved by {names}."


def _header(approval: ExpeditedApproval, title: str) -> list[dict[str, Any]]:
    pr = approval.pull_request
    label = f"{pr.owner}/{pr.repo}#{pr.number}"
    return [
        _section(f"*Expedited review requested*\n<{pr.url}|{label}> {_escape(title)}"),
        _context(
            f"Revision `{approval.head_sha[:12]}` · author @{_escape(pr.author or 'unknown')}"
        ),
    ]


def _diff_sections(files: list[ChangedFile]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for file in files[:_MAX_FILE_SECTIONS]:
        stats = f"+{file.additions} −{file.deletions}"
        sections.append(_section(f"`{_escape(file.filename)}`  {stats}"))
        sections.append(_section(_code_block(file.patch or "")))
    if len(files) > _MAX_FILE_SECTIONS:
        sections.append(_context(f"{len(files) - _MAX_FILE_SECTIONS} more files on GitHub."))
    return sections


def open_card(
    approval: ExpeditedApproval, *, title: str, files: list[ChangedFile]
) -> tuple[str, list[dict[str, Any]]]:
    """Text fallback and blocks for a card that is accepting votes."""
    pr = approval.pull_request
    blocks = [
        *_header(approval, title),
        {"type": "divider"},
        *_diff_sections(files),
        {"type": "divider"},
        _section(_vote_summary(approval)),
        _context(
            "Approve submits a GitHub review as you and, on the second approval, merges the "
            "pull request. The author may approve but that click does not become a GitHub review."
        ),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "style": "primary",
                    "value": _button_value("approve", approval),
                    "action_id": "open_swe_option_select_approve",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Reject and give feedback",
                        "emoji": True,
                    },
                    "style": "danger",
                    "value": _button_value("reject", approval),
                    "action_id": "open_swe_option_select_reject",
                },
            ],
        },
    ]
    text = f"Expedited review requested for {pr.url} ({approval.head_sha[:12]})"
    return text, blocks


def closed_card(
    approval: ExpeditedApproval, *, title: str, files: list[ChangedFile], outcome: str
) -> tuple[str, list[dict[str, Any]]]:
    """Text fallback and blocks for a card whose vote is over."""
    pr = approval.pull_request
    blocks = [
        *_header(approval, title),
        {"type": "divider"},
        *_diff_sections(files),
        {"type": "divider"},
        _section(f"*{_escape(outcome)}*"),
        _context(_vote_summary(approval)),
    ]
    return f"{outcome} — {pr.url}", blocks


def reject_modal(approval: ExpeditedApproval, *, channel_id: str, thread_ts: str) -> dict[str, Any]:
    pr = approval.pull_request
    return {
        "type": "modal",
        "callback_id": REJECT_MODAL_CALLBACK,
        "private_metadata": json.dumps(
            {"approval_id": str(approval.id), "channel_id": channel_id, "thread_ts": thread_ts}
        ),
        "title": {"type": "plain_text", "text": "Reject expedited review"},
        "submit": {"type": "plain_text", "text": "Reject"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            _section(f"Rejecting <{pr.url}|{pr.owner}/{pr.repo}#{pr.number}> ends this vote."),
            {
                "type": "input",
                "block_id": REJECT_FEEDBACK_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "Feedback for the agent"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": REJECT_FEEDBACK_ACTION,
                    "multiline": True,
                    "max_length": 3000,
                    "placeholder": {"type": "plain_text", "text": "What should change?"},
                },
            },
        ],
    }
