import logging

from agent.run_config import RunConfig
from agent.users.models import User

logger = logging.getLogger(__name__)


async def gateway_metadata_for_run(cfg: RunConfig) -> dict[str, str]:
    """Resolve gateway attribution from the invocation's actor, not its thread owner."""
    metadata = {"openswe_user_id": "unattributed"}
    if cfg.thread_id:
        metadata["thread_id"] = cfg.thread_id
    if cfg.invocation_id:
        metadata["invocation_id"] = cfg.invocation_id
    if cfg.background_task_completion:
        return metadata
    try:
        if cfg.github_user_id:
            user = await User.for_identity("github", cfg.github_user_id)
            if user is not None and cfg.github_login:
                login_user = await User.for_login("github", cfg.github_login)
                if login_user is None or login_user.id != user.id:
                    logger.warning("Conflicting gateway actor identities")
                    return metadata
        elif cfg.github_login:
            user = await User.for_login("github", cfg.github_login)
        elif cfg.user_email:
            user = await User.for_email(cfg.user_email)
        else:
            user = None
        if user is not None:
            metadata["openswe_user_id"] = str(user.id)
    except Exception:
        logger.exception("Failed to resolve gateway attribution")
    return metadata
