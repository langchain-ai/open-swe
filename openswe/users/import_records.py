"""Move per-person settings and credentials from the LangGraph Store into ``user_record``.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module. Pending OAuth flows are
not moved: they expire within minutes and a retry starts a new one.
"""

from openswe.dashboard.profiles import GITHUB_OAUTH_TOKENS, PROFILES
from openswe.dashboard.user_credentials import USER_CREDENTIALS
from openswe.dashboard.user_instructions import USER_INSTRUCTIONS
from openswe.dashboard.user_preferences import USER_PREFERENCES
from openswe.database.store_imports import StoreImport


async def import_user_records() -> StoreImport:
    """Move every legacy record whose person has a ``users`` row."""
    result = StoreImport()
    for records, namespace in (
        (PROFILES, ["profiles"]),
        (GITHUB_OAUTH_TOKENS, ["oauth_tokens"]),
        (USER_PREFERENCES, ["user_preferences"]),
        (USER_INSTRUCTIONS, ["user_instructions"]),
    ):
        result += await records.import_store(namespace)
    return result + await USER_CREDENTIALS.import_store(["user_credentials"], nested=True)
