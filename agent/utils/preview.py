"""Preview deployment side-effect guards."""

import logging

from agent.config import ENV

logger = logging.getLogger(__name__)


def skip_on_preview(operation: str) -> bool:
    """Log and return whether an operation should be skipped in preview."""
    if ENV.OPENSWE_ENV.optional() != "preview":
        return False
    logger.info("Skipping operation in preview", extra={"operation": operation})
    return True
