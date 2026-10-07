"""Move per-person settings and credentials from the LangGraph Store into ``user_record``.

Each startup moves what is left and deletes it from the Store, so the import is
idempotent and a later release can delete this module. Pending OAuth flows are
not moved: they expire within minutes and a retry starts a new one.
"""

import logging

from openswe.dashboard.profiles import GITHUB_OAUTH_TOKENS, PROFILES
from openswe.dashboard.user_credentials import USER_CREDENTIALS
from openswe.dashboard.user_instructions import USER_INSTRUCTIONS
from openswe.dashboard.user_preferences import USER_PREFERENCES

logger = logging.getLogger(__name__)


async def import_user_records() -> int:
    """Move every legacy record whose person has a ``users`` row; returns how many moved."""
    moved = 0
    for records, namespace in (
        (PROFILES, ["profiles"]),
        (GITHUB_OAUTH_TOKENS, ["oauth_tokens"]),
        (USER_PREFERENCES, ["user_preferences"]),
        (USER_INSTRUCTIONS, ["user_instructions"]),
    ):
        moved += await records.import_store(namespace)
    moved += await USER_CREDENTIALS.import_store(["user_credentials"], nested=True)
    logger.info("Legacy per-person records processed", extra={"moved_records": moved})
    return moved
