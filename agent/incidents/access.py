"""Responder access shared by incident channels and dashboard routes."""

from agent.config import ENV
from agent.dashboard.admin import is_admin


def is_observability_authorized(email: str | None, *, login: str | None = None) -> bool:
    if is_admin(email, login=login):
        return True
    allowed = {
        entry.strip().lower()
        for entry in ENV.OBSERVABILITY_AUTHORIZED_EMAILS.get().split(",")
        if entry.strip()
    }
    return bool(email and email.strip().lower() in allowed)
