"""Who is allowed to use Open SWE, and therefore to have a user record.

A GitHub login qualifies by being listed in ``ALLOWED_GITHUB_USERS`` or by being
an active member of one of ``ALLOWED_GITHUB_ORGS``. Membership is resolved
through the GitHub App installation token and fails closed.
"""

import hmac
import logging

from openswe.config import ENV
from openswe.github.org_membership import MembershipVerificationError, Org

logger = logging.getLogger(__name__)


class UnauthorizedUser(Exception):
    """A provider account that may not be given a user record."""


class AccountConflict(UnauthorizedUser):
    """A sign in that would give one person both a GitHub and a LangSmith account."""


def allowed_logins() -> tuple[str, ...]:
    return tuple(dict.fromkeys(user.lower() for user in ENV.ALLOWED_GITHUB_USERS.get_list()))


def allowed_orgs() -> tuple[str, ...]:
    return tuple(dict.fromkeys(org.lower() for org in ENV.ALLOWED_GITHUB_ORGS.get_list()))


async def is_authorized_github_login(login: str, *, strict: bool = False) -> bool:
    normalized = login.strip().lower()
    if not normalized:
        return False
    if any(hmac.compare_digest(normalized, allowed) for allowed in allowed_logins()):
        return True
    failures: list[MembershipVerificationError] = []
    for org in allowed_orgs():
        try:
            if await Org(org).verify_active_member(normalized):
                return True
        except MembershipVerificationError as exc:
            failures.append(exc)
    if failures and strict:
        raise MembershipVerificationError(
            rate_limited=any(exc.rate_limited for exc in failures),
            status_codes=tuple(sorted({code for exc in failures for code in exc.status_codes})),
        )
    return False
