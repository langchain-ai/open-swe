"""The one repository-resolution step every channel shares.

Slack and Linear each resolve the repo a trigger targets from their own
channel-specific signals first, but both then fall back to the triggering
person's dashboard ``default_repo``. Only that step lives here; the ordering
around it differs per channel and belongs with the channel.
"""

import logging

from ..dashboard.agent_overrides import get_profile_default_repo, resolve_login_from_email_async

logger = logging.getLogger(__name__)


async def profile_default_repo_for_email(
    email: str | None, *, channel: str
) -> dict[str, str] | None:
    """The dashboard ``default_repo`` of the Open SWE account behind ``email``."""
    try:
        repo_config = await get_profile_default_repo(await resolve_login_from_email_async(email))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to apply dashboard default_repo for %s user", channel)
        return None
    if repo_config:
        logger.info(
            "Applying dashboard default_repo for %s user %s: %s/%s",
            channel,
            email,
            repo_config["owner"],
            repo_config["name"],
        )
    return repo_config
