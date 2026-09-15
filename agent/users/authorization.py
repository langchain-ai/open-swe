"""Who is allowed to use Open SWE, and therefore to have a user record.

A GitHub login qualifies by being listed in ``ALLOWED_GITHUB_USERS`` or by being
an active member of one of ``ALLOWED_GITHUB_ORGS``. Membership is resolved
through the GitHub App installation token and fails closed.
"""

import hmac
import logging

from agent.config import ENV
from agent.github.org_membership import is_user_active_org_member

logger = logging.getLogger(__name__)


class UnauthorizedUser(Exception):
    """A provider account that may not be given a user record."""


def allowed_logins() -> tuple[str, ...]:
    return tuple(dict.fromkeys(user.lower() for user in ENV.ALLOWED_GITHUB_USERS.get_list()))


def allowed_orgs() -> tuple[str, ...]:
    return tuple(dict.fromkeys(org.lower() for org in ENV.ALLOWED_GITHUB_ORGS.get_list()))


async def is_authorized_github_login(login: str) -> bool:
    normalized = login.strip().lower()
    if not normalized:
        return False
    if any(hmac.compare_digest(normalized, allowed) for allowed in allowed_logins()):
        return True
    for org in allowed_orgs():
        if await is_user_active_org_member(normalized, org):
            return True
    return False
