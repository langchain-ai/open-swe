"""Admin gate driven by the CONFIGURED_ADMINS env var."""

from agent.config import ENV


def configured_admins() -> frozenset[str]:
    """Lowercased GitHub logins and emails from ``CONFIGURED_ADMINS``."""
    raw = ENV.CONFIGURED_ADMINS.get()
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def _admin_identities(email: str | None, login: str | None) -> frozenset[str]:
    return frozenset(
        value.strip().lower()
        for value in (email, login)
        if isinstance(value, str) and value.strip()
    )


def is_admin(email: str | None, *, login: str | None = None) -> bool:
    return bool(_admin_identities(email, login) & configured_admins())
