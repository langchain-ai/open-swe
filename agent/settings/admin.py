"""Admin gate driven by the CONFIGURED_ADMINS env var."""

from ..config import configured_admins, observability_authorized_emails


def _admin_identities(email: str | None, login: str | None) -> frozenset[str]:
    return frozenset(
        value.strip().lower()
        for value in (email, login)
        if isinstance(value, str) and value.strip()
    )


def is_admin(email: str | None, *, login: str | None = None) -> bool:
    return bool(_admin_identities(email, login) & configured_admins())


def is_observability_authorized(email: str | None, *, login: str | None = None) -> bool:
    """Whether a user may use the team observability tools."""
    identities = _admin_identities(email, login)
    if identities & configured_admins():
        return True
    if not email:
        return False
    return email.strip().lower() in observability_authorized_emails()
