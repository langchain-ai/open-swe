"""The Slack message people vote on: the whole diff plus Approve and Dismiss."""

import json

from openswe.expedited_review.eligibility import ChangedFile
from openswe.human_review.requests import ChannelChoice, HumanReviewRequest
from openswe.slack.blocks import (
    SECTION_TEXT_MAX_CHARS,
    Block,
    ButtonElement,
    actions,
    button,
    code_block,
    context,
    divider,
    escape,
    image,
    option,
    section,
    static_select,
)

BUTTON_TYPE = "expedited_review"
SEND_BLOCK_ID = "expedited_review_send"
CHANNEL_SELECT_ACTION = "expedited_review_channel"
OTHER_CHANNEL = "other"

_MAX_FILE_SECTIONS = 20
# Slack refuses a section over 3000 characters, and refusing means no card at all.
_MAX_PATCH_LINES = 60
_OVERFLOW_NOTE_RESERVE = 64


def _button_value(action: str, approval: HumanReviewRequest) -> str:
    return json.dumps({"type": BUTTON_TYPE, "action": action, "fingerprint": str(approval.id)})


def _vote_summary(approval: HumanReviewRequest, author: str) -> str:
    """``author`` and the approvers are Slack mentions, so none of this is escaped."""
    if not approval.approvals:
        return f"Needs one approval from someone other than {author}."
    return f"Approved by {', '.join(vote.slack_mention for vote in approval.approvals)}."


def _header(approval: HumanReviewRequest, title: str, author: str) -> list[Block]:
    pr = approval.pull_request
    label = f"{pr.owner}/{pr.repo}#{pr.number}"
    blocks = [section(f"*Expedited review requested*\n<{pr.url}|{label}> {escape(title)}")]
    if approval.tldr:
        blocks.append(section("\n".join(f">{line}" for line in escape(approval.tldr).splitlines())))
    return [*blocks, context(f"Author {author}")]


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
    heading = "*Tests (not shown)*\n"
    budget = SECTION_TEXT_MAX_CHARS - len(heading) - _OVERFLOW_NOTE_RESERVE
    lines: list[str] = []
    for file in tests[:_MAX_FILE_SECTIONS]:
        line = f"`{escape(file.filename)}`  +{file.additions} −{file.deletions}"
        budget -= len(line) + 1
        if budget < 0:
            break
        lines.append(line)
    if len(lines) < len(tests):
        lines.append(f"{len(tests) - len(lines)} more test files on GitHub.")
    return [context(heading + "\n".join(lines))]


def _vote_buttons(approval: HumanReviewRequest) -> list[ButtonElement]:
    approve = button(
        "Approve",
        action_id="open_swe_option_select_approve",
        value=_button_value("approve", approval),
        style="primary",
    )
    return [approve, _dismiss_button(approval)]


def _send_controls(approval: HumanReviewRequest, choices: list[ChannelChoice]) -> list[Block]:
    """Send the card to its own channel or another one; offered once, while it awaits a vote."""
    if not choices or approval.sent_elsewhere:
        return []
    configured_only = len(choices) == 1 and choices[0]["id"] != approval.slack_channel_id
    other = button(
        "Other channel…",
        action_id="open_swe_option_select_other",
        value=_button_value("other", approval),
    )
    if len(choices) == 1:
        broadcast = button(
            f"Broadcast in #{choices[0]['name']}",
            action_id="open_swe_option_select_broadcast",
            value=_button_value("broadcast", approval),
        )
        return [actions(broadcast, *([] if configured_only else [other]), block_id=SEND_BLOCK_ID)]
    options = [option(f"#{choice['name']}", choice["id"]) for choice in choices]
    picker = static_select(
        action_id=CHANNEL_SELECT_ACTION,
        options=[*options, option("Other…", f"{OTHER_CHANNEL}:{approval.id}")],
        initial=options[0],
    )
    send = button(
        "Send",
        action_id="open_swe_option_select_send",
        value=_button_value("send", approval),
    )
    return [actions(picker, send, block_id=SEND_BLOCK_ID)]


def _ready_button(approval: HumanReviewRequest) -> ButtonElement:
    return button(
        "Mark ready for review",
        action_id="open_swe_option_select_ready",
        value=_button_value("ready", approval),
        style="primary",
    )


def readiness_prompt(
    approval: HumanReviewRequest,
    *,
    title: str,
    author: str,
    files: list[ChangedFile],
    diff_image_id: str | None = None,
) -> tuple[str, list[Block]]:
    """The full author-only draft card."""
    pr = approval.pull_request
    text = f"Mark {pr.url} ready for review so someone else can approve it."
    return text, [
        *_header(approval, title, author),
        divider(),
        *_voting_diff(approval, files, diff_image_id),
        section("*Draft.* Mark it ready for review to request an approval in the thread."),
        actions(_ready_button(approval), _dismiss_button(approval)),
    ]


def _dismiss_button(approval: HumanReviewRequest) -> ButtonElement:
    return button(
        "Dismiss",
        action_id="open_swe_option_select_dismiss",
        value=_button_value("dismiss", approval),
    )


def _voting_diff(
    approval: HumanReviewRequest, files: list[ChangedFile], diff_image_id: str | None
) -> list[Block]:
    """The diff voters read; an approved card no longer needs it."""
    if approval.approved:
        return []
    return [*_diff_sections(files, diff_image_id), divider()]


def _status(approval: HumanReviewRequest, author: str, choices: list[ChannelChoice]) -> list[Block]:
    if approval.awaiting_ready:
        return [
            section(f"*Draft.* Waiting for {author} to mark it ready for review."),
            actions(_dismiss_button(approval)),
        ]
    return [
        section(_vote_summary(approval, author)),
        actions(*_vote_buttons(approval)),
        *_send_controls(approval, choices),
    ]


def open_card(
    approval: HumanReviewRequest,
    *,
    title: str,
    author: str,
    files: list[ChangedFile],
    diff_image_id: str | None = None,
    choices: list[ChannelChoice] | None = None,
    thread_url: str | None = None,
) -> tuple[str, list[Block]]:
    """Text fallback and blocks for an open card; an approved one collapses to one line.

    ``author`` is the PR author's Slack mention, from :meth:`HumanReviewRequest.author_mention`.
    ``choices`` are the channels the card offers to be sent to, its own first. ``thread_url``
    marks the copy posted in another channel, which links back and offers no sending.
    """
    pr = approval.pull_request
    if approval.approved and not approval.awaiting_ready:
        label = f"{pr.owner}/{pr.repo}#{pr.number}"
        heading = f"Expedited review: {_vote_summary(approval, author)}"
        return f"{heading} — {pr.url}", [
            section(f":white_check_mark: *{heading}*\n<{pr.url}|{label}> {escape(title)}")
        ]
    header = _header(approval, title, author)
    if thread_url:
        header.append(context(f"Sent from <{thread_url}|this thread>."))
    blocks: list[Block] = [
        *header,
        divider(),
        *_voting_diff(approval, files, diff_image_id),
        *_status(approval, author, [] if thread_url is not None else choices or []),
    ]
    text = f"Expedited review requested for {pr.url}"
    return text, blocks


def closed_card(
    approval: HumanReviewRequest,
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
