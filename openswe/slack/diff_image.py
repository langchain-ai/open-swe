"""Render and host a diff without posting a separate Slack file message."""

import asyncio
import logging
from dataclasses import dataclass

from openswe.expedited_review.diff_image import render_diff_png
from openswe.expedited_review.eligibility import ChangedFile
from openswe.slack.client import upload_slack_thread_file, wait_for_slack_file
from openswe.utils.startup_trace import apm_span

logger = logging.getLogger(__name__)


@dataclass
class SlackDiffImage:
    files: list[ChangedFile]
    filename: str

    async def upload(self, tags: dict[str, object]) -> str | None:
        """Return a ready Slack file id, or let the caller fall back to text."""
        if not self.files:
            return None
        try:
            with apm_span("slack.diff_image.render", tags):
                png = await asyncio.to_thread(render_diff_png, self.files)
            with apm_span("slack.diff_image.upload", tags):
                file_id = await upload_slack_thread_file(
                    None, None, self.filename, png, title="Diff"
                )
            with apm_span("slack.diff_image.processing", tags):
                ready = await wait_for_slack_file(file_id)
            if ready:
                return file_id
            logger.warning(
                "Slack did not finish processing the diff image",
                extra={"slack_file_id": file_id},
            )
        except Exception:
            logger.warning("Could not render or upload a Slack diff image", exc_info=True)
        return None
