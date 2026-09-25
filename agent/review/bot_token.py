"""Re-mint the reviewer's bot GitHub token when the one cached at run start has expired."""

import logging

from agent.github.app import get_github_app_installation_token_with_expiry
from agent.github.thread_token import cache_github_token_for_thread
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


async def mint_reviewer_github_token(cfg: RunConfig) -> str | None:
    """Mint and cache a fresh bot token for a webhook-triggered reviewer thread."""
    if not cfg.source or not cfg.thread_id:
        return None
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repositories=[cfg.repo.name] if cfg.repo else None
    )
    if not token:
        logger.warning(
            "Could not re-mint reviewer GitHub token", extra={"thread_id": cfg.thread_id}
        )
        return None
    logger.info("Re-minted expired reviewer GitHub token", extra={"thread_id": cfg.thread_id})
    cache_github_token_for_thread(cfg.thread_id, token, expires_at=expires_at, is_bot_token=True)
    return token
