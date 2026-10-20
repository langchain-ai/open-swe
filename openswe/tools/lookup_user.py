"""Find people Open SWE knows, and resolve the ``user:<uuid>`` ids every person-naming tool takes."""

import re
from typing import Any

from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User

_SLACK_MEMBER = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>|([UW][A-Z0-9]{6,})")
_MAX_MATCHES = 10


class UnknownUser(ValueError):
    """A tool was handed something other than a known person's ``user:<uuid>`` id."""

    def __init__(self, ref: str) -> None:
        super().__init__(
            f"{ref!r} is not an Open SWE user id. Call lookup_user to find the person's "
            "`user:<uuid>` id, then pass that."
        )


async def user_for_ref(ref: str) -> User:
    """The person ``ref`` names; raises :class:`UnknownUser` for anything but a known id."""
    user = await User.for_ref(ref)
    if user is None:
        raise UnknownUser(ref)
    return user


async def github_login_for_ref(ref: str) -> str:
    """The GitHub login of the person ``ref`` names; raises ``ValueError`` when there is none."""
    user = await user_for_ref(ref)
    if not user.github_login:
        raise ValueError(f"{user.display_name or ref} has no linked GitHub account.")
    return user.github_login


async def slack_ids_for_refs(refs: list[str]) -> list[str]:
    """The Slack member ids of the people ``refs`` name; raises ``ValueError`` for any without one."""
    ids: list[str] = []
    for ref in refs:
        user = await user_for_ref(ref)
        if not user.slack_user_id:
            raise ValueError(f"{user.display_name or ref} has no linked Slack account.")
        ids.append(user.slack_user_id)
    return ids


async def _matches(query: str) -> list[User]:
    if (user := await User.for_ref(query)) is not None:
        return [user]
    if member := _SLACK_MEMBER.fullmatch(query):
        user = await User.for_identity("slack", member[1] or member[2])
        return [user] if user is not None else []
    handle = query.removeprefix("@")
    if "@" in handle:
        user = await User.for_email(handle)
        return [user] if user is not None else []
    exact = await User.for_login("github", handle)
    found, _ = await User.page(offset=0, limit=_MAX_MATCHES, search=handle)
    unique = {user.id: user for user in ([exact] if exact is not None else []) + found}
    return list(unique.values())[:_MAX_MATCHES]


@expose_mcp()
async def lookup_user(query: str) -> dict[str, Any]:
    """Implement the `lookup_user` tool."""
    query = query.strip()
    if not query:
        return {"success": False, "error": "query is required"}
    users = await _matches(query)
    return {
        "success": True,
        "users": [
            {
                "user_id": user.ref,
                "display_name": user.display_name,
                "github_login": user.github_login,
                "slack_user_id": user.slack_user_id,
            }
            for user in users
        ],
        "next": (
            "Pass the person's user_id to any tool that names a person. If several match and "
            "the request does not say which, ask."
            if users
            else "Nobody Open SWE knows matches; they may not have signed in to Open SWE yet."
        ),
    }
