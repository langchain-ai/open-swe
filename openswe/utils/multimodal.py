"""Images in the messages Open SWE's own Slack app receives, fetched with its token."""

import logging
from urllib.parse import urlparse

import httpx2
from langchain_core.messages.content import ImageContentBlock, TextContentBlock
from openswe_shared import images

from openswe.config import ENV

logger = logging.getLogger(__name__)


def vision_not_supported_warning(model_id: str, image_count: int) -> str:
    """Build a prompt-visible warning when images are sent to a text-only model."""
    return (
        f"\n\n**Note:** {image_count} image(s) were attached but the current model "
        f"({model_id}) does not support image input. The images were not included. "
        "Please switch to a vision-enabled model to process images."
    )


def _is_slack_file(image_url: str) -> bool:
    host = (urlparse(image_url).hostname or "").lower()
    return host == "files.slack.com" or host.endswith(".files.slack.com")


def _image_auth_headers_for_url(original_url: str, current_url: str) -> dict[str, str] | None:
    if not (_is_slack_file(original_url) and _is_slack_file(current_url)):
        return None
    slack_bot_token = ENV.SLACK_BOT_TOKEN.get()
    if slack_bot_token:
        return {"Authorization": f"Bearer {slack_bot_token}"}
    logger.warning(
        "SLACK_BOT_TOKEN not set; cannot authenticate image fetch for %s",
        current_url,
    )
    return None


async def fetch_image_block(
    image_url: str,
    client: httpx2.AsyncClient,
) -> ImageContentBlock | TextContentBlock | None:
    """Fetch an image as a model content block, a Slack file with the bot's token."""
    return await images.fetch_image_block(
        image_url, client, headers_for_url=_image_auth_headers_for_url
    )
