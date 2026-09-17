"""The one place a ``PersonIdentity`` becomes a ``User``.

Every ingress — Slack events and clicks, GitHub webhooks, run dispatch — names
people as ``PersonIdentity`` records whose ``id`` is ``<platform>:<external id>``.
Resolution never creates a person: users come into being when they sign in, so
an unknown identity resolves to ``None`` and the caller decides what that means.

The legacy Slack→GitHub mapping store is consulted only when the users table has
no Slack identity yet; a hit links that Slack identity to the person, so the
fallback retires itself one click at a time.
"""

import logging

from agent.dashboard.user_mappings import login_for_slack_id
from agent.input_messages import PersonIdentity
from agent.users.models import User

logger = logging.getLogger(__name__)


def split_identity(person: PersonIdentity) -> tuple[str, str]:
    """``(platform, external id)`` from ``person["id"]``; platform may be empty."""
    platform, separator, external_id = person["id"].partition(":")
    if not separator:
        return person.get("platform", ""), platform
    return platform, external_id


async def resolve_person(person: PersonIdentity) -> User | None:
    platform, external_id = split_identity(person)
    if platform == "slack" and external_id:
        return await _resolve_slack(external_id, person)
    if platform == "github" and external_id:
        return await _resolve_github(external_id, person)
    login = person.get("github_login", "")
    return await User.for_login("github", login) if login else None


async def _resolve_slack(slack_user_id: str, person: PersonIdentity) -> User | None:
    user = await User.for_identity("slack", slack_user_id)
    if user is not None:
        return user
    login = person.get("github_login") or await login_for_slack_id(slack_user_id) or ""
    if not login:
        return None
    user = await User.for_login("github", login)
    if user is None:
        return None
    try:
        return await user.link("slack", slack_user_id, login=person.get("handle", ""))
    except Exception:
        logger.warning(
            "Could not link Slack identity found through the legacy mapping",
            extra={"user_id": str(user.id)},
            exc_info=True,
        )
        return user


async def _resolve_github(external_id: str, person: PersonIdentity) -> User | None:
    if external_id.isdigit():
        user = await User.for_identity("github", external_id)
        if user is not None:
            return user
    login = person.get("github_login") or (external_id if not external_id.isdigit() else "")
    return await User.for_login("github", login) if login else None
