"""File-by-file expedited review with approval only on the final page."""

import asyncio
import hashlib
import logging
from uuid import UUID

from fastapi import BackgroundTasks
from pydantic import BaseModel, ValidationError

from agent.expedited_review.eligibility import (
    ChangedFile,
    EligibleDiff,
    assess_eligibility,
    fetch_changed_files,
    fingerprint_matches,
)
from agent.expedited_review.voting import process_vote
from agent.human_review.people import repo_token
from agent.human_review.requests import HumanReviewRequest
from agent.slack.blocks import ModalView, code_blocks, context, escape, modal, section, view_payload
from agent.slack.http import SLACK_REQUEST_ERRORS, SlackClient
from agent.slack.payloads import SlackInteraction
from agent.slack.responses import WebhookResponse, accepted, ignored
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

CALLBACK = "expedited_review_files"


class ReviewOrigin(BaseModel):
    approval_id: UUID
    channel_id: str
    thread_ts: str
    user_id: str
    fingerprint: str
    page: int = 0
    files_fingerprint: str = ""
    final_page: bool = False


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
    if not pin_files(origin, files):
        return None
    return render_page(origin, files, pr.url, pr.owner + "/" + pr.repo, pr.number)


def pin_files(origin: ReviewOrigin, files: list[ChangedFile]) -> bool:
    fingerprint = hashlib.sha256(
        "\0".join(file.model_dump_json() for file in files).encode()
    ).hexdigest()
    if origin.files_fingerprint and origin.files_fingerprint != fingerprint:
        return False
    origin.files_fingerprint = fingerprint
    return True


def render_page(
    origin: ReviewOrigin, files: list[ChangedFile], url: str, repo: str, number: int
) -> ModalView | None:
    if not 0 <= origin.page < len(files):
        return None
    file = files[origin.page]
    blocks = [
        section(f"<{url}|{escape(repo)}#{number}>"),
        context(f"File {origin.page + 1} of {len(files)}"),
        section(f"`{escape(file.filename)}`  +{file.additions} −{file.deletions}"),
        *code_blocks(file.patch or "No text patch available for this test file."),
    ]
    if len(blocks) > 100:
        return None
    origin.final_page = origin.page == len(files) - 1
    return modal(
        callback_id=CALLBACK,
        title="Expedited review",
        blocks=blocks,
        submit="Approve" if origin.page == len(files) - 1 else "Next file",
        close="Cancel",
        private_metadata=origin.model_dump_json(),
    )


def loading_view() -> ModalView:
    return modal(
        callback_id=CALLBACK,
        title="Expedited review",
        blocks=[section("Loading review…")],
        close="Cancel",
    )


def unavailable_view() -> ModalView:
    return modal(
        callback_id=CALLBACK,
        title="Review unavailable",
        blocks=[
            section(
                "The diff changed, this review closed, or the files could not be loaded. Reopen the current card."
            )
        ],
        close="Close",
    )


async def update_page(origin: ReviewOrigin, view_id: str, *, approve: bool = False) -> None:
    try:
        view = await review_page(origin)
        if view is not None and approve:
            await process_vote(
                str(origin.approval_id),
                decision="approve",
                person={"id": f"slack:{origin.user_id}"},
                channel_id=origin.channel_id,
                thread_ts=origin.thread_ts,
            )
            view = modal(
                callback_id=CALLBACK,
                title="Expedited review",
                blocks=[section("Approval processed. Check the card for the result.")],
                close="Close",
            )
        async with SlackClient.bot() as client:
            await client.views_update(
                view_id=view_id, view=view_payload(view or unavailable_view())
            )
    except Exception:
        logger.exception("Failed to load expedited review page")
        try:
            async with SlackClient.bot() as client:
                await client.views_update(view_id=view_id, view=view_payload(unavailable_view()))
        except SLACK_REQUEST_ERRORS:
            logger.warning("Failed to show expedited review error", exc_info=True)


async def open_review(
    interaction: SlackInteraction, approval_id: str, background_tasks: BackgroundTasks
) -> WebhookResponse:
    try:
        request_id = UUID(approval_id)
    except ValueError:
        logger.warning("Invalid expedited review id", exc_info=True)
        return ignored("Invalid review")
    if not interaction.trigger_id:
        return ignored("Missing trigger")
    try:
        async with asyncio.timeout(2), SlackClient.bot() as client:
            response = await client.views_open(
                trigger_id=interaction.trigger_id, view=view_payload(loading_view())
            )
    except SLACK_REQUEST_ERRORS:
        logger.warning("Failed to open expedited review", exc_info=True)
        return ignored("Could not open review")
    view_id = str(response["view"]["id"])
    approval = await HumanReviewRequest.get(request_id)
    origin = ReviewOrigin(
        approval_id=request_id,
        channel_id=interaction.channel_id,
        thread_ts=interaction.thread_ts,
        user_id=interaction.user.id,
        fingerprint=approval.diff_fingerprint if approval else "",
    )
    background_tasks.add_task(update_page, origin, view_id)
    return accepted("Review opened")


async def submit_page(
    interaction: SlackInteraction, background_tasks: BackgroundTasks
) -> JsonObject:
    try:
        origin = ReviewOrigin.model_validate_json(interaction.view.private_metadata)
    except ValidationError:
        logger.warning("Expedited review lost its origin", exc_info=True)
        return {"response_action": "update", "view": view_payload(unavailable_view())}
    if interaction.user.id != origin.user_id:
        return {"response_action": "clear"}
    if not origin.final_page:
        origin.page += 1
    background_tasks.add_task(update_page, origin, interaction.view.id, approve=origin.final_page)
    return {"response_action": "update", "view": view_payload(loading_view())}
