"""People and the provider identities they sign in with."""

from openswe.users.authorization import UnauthorizedUser, is_authorized_github_login
from openswe.users.display_name_backfill import persist_display_name
from openswe.users.models import Provider, User, UserIdentity
from openswe.users.preferences import UserPreferences, UserPreferencesPatch

__all__ = [
    "Provider",
    "UnauthorizedUser",
    "User",
    "UserIdentity",
    "UserPreferences",
    "UserPreferencesPatch",
    "is_authorized_github_login",
    "persist_display_name",
]
