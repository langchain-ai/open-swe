"""Admin gate driven by the CONFIGURED_ADMINS env var."""

from coding_agent.config import ENV


def _configured_admins() -> frozenset[str]:
    raw = ENV.CONFIGURED_ADMINS.get()
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def _admin_identities(email: str | None, login: str | None) -> frozenset[str]:
    return frozenset(
        value.strip().lower()
        for value in (email, login)
        if isinstance(value, str) and value.strip()
    )


def is_admin(email: str | None, *, login: str | None = None) -> bool:
    return bool(_admin_identities(email, login) & _configured_admins())
