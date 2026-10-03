"""File-by-file expedited review with approval only on the final page."""

from uuid import UUID

from fastapi import BackgroundTasks
from pydantic import BaseModel, ValidationError

from agent.expedited_review.eligibility import (
    EligibleDiff,
    assess_eligibility,
    fetch_changed_files,
    fingerprint_matches,
)
from agent.expedited_review.voting import process_vote
from agent.human_review.people import repo_token
from agent.human_review.requests import HumanReviewRequest
from agent.slack.blocks import ModalView, code_blocks, context, escape, modal, section, view_payload
from agent.slack.client import open_slack_modal
from agent.slack.payloads import SlackInteraction
from agent.slack.responses import WebhookResponse, accepted, ignored
from agent.utils.json_types import JsonObject

CALLBACK = "expedited_review_files"


class ReviewOrigin(BaseModel):
    approval_id: UUID
    channel_id: str
    thread_ts: str
    user_id: str
    fingerprint: str
    page: int = 0


async def review_page(origin: ReviewOrigin) -> ModalView | None:
    approval = await HumanReviewRequest.get(origin.approval_id)
    if (
        approval is None
        or approval.kind != "expedited"
        or approval.state != "open"
        or approval.awaiting_ready
    ):
        return None
    if origin.channel_id not in {approval.slack_channel_id, approval.slack_copy_channel_id}:
        return None
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return None
    files = await fetch_changed_files(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    if files is None or not isinstance(assess_eligibility(files), EligibleDiff):
        return None
    if origin.fingerprint != approval.diff_fingerprint or not fingerprint_matches(
        files, origin.fingerprint
    ):
        return None
    if not 0 <= origin.page < len(files):
        return None
    file = files[origin.page]
    blocks = [
        section(f"<{pr.url}|{escape(pr.owner + '/' + pr.repo)}#{pr.number}>"),
        context(f"File {origin.page + 1} of {len(files)}"),
        section(f"`{escape(file.filename)}`  +{file.additions} −{file.deletions}"),
        *code_blocks(file.patch or "No text patch available for this test file."),
    ]
    if len(blocks) > 100:
        return None
    return modal(
        callback_id=CALLBACK,
        title="Expedited review",
        blocks=blocks,
        submit="Approve" if origin.page == len(files) - 1 else "Next file",
        close="Cancel",
        private_metadata=origin.model_dump_json(),
    )


async def open_review(interaction: SlackInteraction, approval_id: str) -> WebhookResponse:
    try:
        approval = await HumanReviewRequest.get(UUID(approval_id))
    except ValueError:
        return ignored("Invalid review")
    if approval is None:
        return ignored("Review unavailable")
    origin = ReviewOrigin(
        approval_id=approval.id,
        channel_id=interaction.channel_id,
        thread_ts=interaction.thread_ts,
        user_id=interaction.user.id,
        fingerprint=approval.diff_fingerprint,
    )
    view = await review_page(origin)
    if (
        view is None
        or not interaction.trigger_id
        or not await open_slack_modal(interaction.trigger_id, view_payload(view))
    ):
        return ignored("Review unavailable; reopen an up-to-date card")
    return accepted("Review opened")


async def submit_page(
    interaction: SlackInteraction, background_tasks: BackgroundTasks
) -> JsonObject:
    try:
        origin = ReviewOrigin.model_validate_json(interaction.view.private_metadata)
    except ValidationError:
        return {"response_action": "clear"}
    if interaction.user.id != origin.user_id:
        return {"response_action": "clear"}
    current = await review_page(origin)
    if current is None:
        return {
            "response_action": "update",
            "view": view_payload(
                modal(
                    callback_id=CALLBACK,
                    title="Review unavailable",
                    blocks=[
                        section("The diff changed or this review closed. Reopen the current card.")
                    ],
                    close="Close",
                )
            ),
        }
    if current.get("submit", {}).get("text") == "Next file":
        origin.page += 1
        next_page = await review_page(origin)
        if next_page is not None:
            return {"response_action": "update", "view": view_payload(next_page)}
        return {"response_action": "clear"}
    background_tasks.add_task(
        process_vote,
        str(origin.approval_id),
        decision="approve",
        person={"id": f"slack:{origin.user_id}"},
        channel_id=origin.channel_id,
        thread_ts=origin.thread_ts,
    )
    return {}
