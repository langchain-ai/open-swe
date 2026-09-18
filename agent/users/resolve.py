"""The one place a ``PersonIdentity`` becomes a ``User``.

Every ingress — Slack events and clicks, GitHub webhooks, run dispatch — names
people as ``PersonIdentity`` records whose ``id`` is ``<platform>:<external id>``.
Resolution never creates a person: users come into being when they sign in, so
an unknown identity resolves to ``None`` and the caller decides what that means.
"""

from agent.input_messages import PersonIdentity
from agent.users.models import User


def split_identity(person: PersonIdentity) -> tuple[str, str]:
    """``(platform, external id)`` from ``person["id"]``; platform may be empty."""
    platform, separator, external_id = person["id"].partition(":")
    if not separator:
        return person.get("platform", ""), platform
    return platform, external_id


async def resolve_person(person: PersonIdentity) -> User | None:
    platform, external_id = split_identity(person)
    if platform == "slack" and external_id:
        return await User.for_identity("slack", external_id)
    if platform == "github" and external_id:
        return await _resolve_github(external_id, person)
    login = person.get("github_login", "")
    return await User.for_login("github", login) if login else None


async def _resolve_github(external_id: str, person: PersonIdentity) -> User | None:
    if external_id.isdigit():
        user = await User.for_identity("github", external_id)
        if user is not None:
            return user
    login = person.get("github_login") or (external_id if not external_id.isdigit() else "")
    return await User.for_login("github", login) if login else None
