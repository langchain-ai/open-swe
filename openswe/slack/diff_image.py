"""Render and host a diff without posting a separate Slack file message."""

import asyncio
import logging
from dataclasses import dataclass

from openswe.expedited_review.diff_image import render_diff_png
from openswe.expedited_review.eligibility import ChangedFile
from openswe.slack.client import upload_slack_thread_file, wait_for_slack_file

logger = logging.getLogger(__name__)


@dataclass
class SlackDiffImage:
    files: list[ChangedFile]
    filename: str

    async def upload(self) -> str | None:
        """Return a ready Slack file id, or let the caller fall back to text."""
        if not self.files:
            return None
        try:
            png = await asyncio.to_thread(render_diff_png, self.files)
            file_id = await upload_slack_thread_file(None, None, self.filename, png, title="Diff")
            if await wait_for_slack_file(file_id):
                return file_id
            logger.warning(
                "Slack did not finish processing the diff image",
                extra={"slack_file_id": file_id},
            )
        except Exception:
            logger.warning("Could not render or upload a Slack diff image", exc_info=True)
        return None
