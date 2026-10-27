"""Map a channel's verified sender to one of the app's users."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from typing_extensions import TypedDict

from managed_deepagents._agent_auth import WORKSPACE_SETTING
from managed_deepagents._agent_auth_http import ConnectionsNotConfiguredError
from managed_deepagents._channels.runtime import create_runtime_channel
from managed_deepagents._connections import (
    TRUSTED_BACKEND_PROVIDER,
    resolve_external_principal_id,
    resolve_workspace_id,
)
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from managed_deepagents._agent_auth import ResolvePrincipalIdentity
    from managed_deepagents._channels.actor import ChannelActor
    from managed_deepagents._channels.runtime import ChannelMessagingAdapter
    from managed_deepagents._channels.types import ChannelMessage, JsonValue


class ResolveUserChannel(TypedDict):
    name: str


class ResolveUserRequest(TypedDict):
    """What ``resolve_user`` gets: the verified sender of a channel delivery."""

    actor: ChannelActor | None
    """``None`` when the channel could not verify who sent the delivery."""
    channel: ResolveUserChannel


class ResolvedAppUser(TypedDict):
    """One of your users: the principal a backend call with ``auth="backend"`` gets."""

    type: Literal["user"]
    id: str


class ResolvedIdentity(TypedDict):
    """An exact Agent Auth identity, such as the IdP ``sub`` your web users sign in with."""

    type: Literal["identity"]
    provider: str
    issuer: str
    subject: str


class ResolvedReply(TypedDict):
    """Start nothing, and post ``message`` where the delivery came from, as
    ``runtime.channel.post`` would."""

    type: Literal["reply"]
    message: ChannelMessage


class ResolvedIgnore(TypedDict):
    """Start nothing."""

    type: Literal["ignore"]


ResolveUserResult: TypeAlias = (
    ResolvedAppUser | ResolvedIdentity | ResolvedReply | ResolvedIgnore | None
)
"""Who a channel delivery runs as; ``None`` keeps the channel's own principal."""

ResolveUser: TypeAlias = Callable[
    [ResolveUserRequest], ResolveUserResult | Awaitable[ResolveUserResult]
]
"""Map a channel's verified sender to one of your users."""


class ChannelPrincipal(TypedDict):
    type: Literal["principal"]
    id: str


ChannelUser: TypeAlias = ChannelPrincipal | ResolvedReply | ResolvedIgnore
"""What a delivery does after ``resolve_user``."""


def checked_resolve_user(owner: str, resolve_user: ResolveUser | None) -> ResolveUser | None:
    """A channel's own ``resolve_user``; one that isn't callable raises."""
    if resolve_user is not None and not callable(resolve_user):
        raise TypeError(f"{owner}(...) resolve_user must be callable")
    return resolve_user


async def resolve_channel_user(
    resolve_user: ResolveUser | None,
    actor: ChannelActor | None,
    channel_name: str,
    resolve_principal: Callable[[ResolvePrincipalIdentity], str] | None = None,
) -> ChannelUser | None:
    """Ask ``resolve_user`` about the actor; ``None`` keeps the channel's principal."""
    if resolve_user is None:
        return None
    request: ResolveUserRequest = {"actor": actor, "channel": {"name": channel_name}}
    # A synchronous hook may block, so it runs off the event loop.
    result = (
        resolve_user(request)
        if inspect.iscoroutinefunction(resolve_user)
        else await asyncio.to_thread(resolve_user, request)
    )
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return None
    identity: ResolvePrincipalIdentity
    match result.get("type"):
        case "user":
            identity = _app_user_identity(cast("ResolvedAppUser", result)["id"])
        case "identity":
            chosen = cast("ResolvedIdentity", result)
            identity = {
                "provider": chosen["provider"],
                "issuer": chosen["issuer"],
                "subject": chosen["subject"],
            }
        case "reply":
            return cast("ResolvedReply", result)
        case "ignore":
            return {"type": "ignore"}
        case _:
            raise TypeError("resolve_user must return user, identity, reply, ignore or None")
    principal_id = await asyncio.to_thread(
        resolve_principal or resolve_external_principal_id, identity
    )
    return {"type": "principal", "id": principal_id}


# Deliveries already replied to; a provider may redeliver one.
_replied_deliveries: dict[str, None] = {}
_REPLIED_DELIVERIES_KEPT = 1000


async def post_reply(
    message: ChannelMessage,
    *,
    name: str,
    provider: str,
    target: JsonValue,
    transport: ChannelMessagingAdapter | None,
    delivery_key: str | None = None,
) -> None:
    """Post ``resolve_user``'s reply through the path ``runtime.channel.post`` takes,
    once per ``delivery_key``."""
    if delivery_key is not None:
        if delivery_key in _replied_deliveries:
            return
        _replied_deliveries[delivery_key] = None
        if len(_replied_deliveries) > _REPLIED_DELIVERIES_KEPT:
            del _replied_deliveries[next(iter(_replied_deliveries))]
    post = create_runtime_channel(
        name=name, provider=provider, event=None, target=target, transport=transport
    ).post
    if post is None:
        logger.warning("channel cannot post resolve_user's reply")
        return
    try:
        await post(message)
    except Exception as error:  # noqa: BLE001 - a failed reply must not fail the delivery
        logger.error("channel resolve_user reply failed (%s)", logger.safe_error_label(error))


def _app_user_identity(user_id: str) -> ResolvePrincipalIdentity:
    workspace_id = resolve_workspace_id()
    if workspace_id is None:
        raise ConnectionsNotConfiguredError(WORKSPACE_SETTING)
    return {"provider": TRUSTED_BACKEND_PROVIDER, "issuer": workspace_id, "subject": user_id}


__all__ = [
    "ChannelUser",
    "ResolveUser",
    "ResolveUserRequest",
    "ResolveUserResult",
    "ResolvedAppUser",
    "ResolvedIdentity",
    "ResolvedIgnore",
    "ResolvedReply",
    "checked_resolve_user",
    "post_reply",
    "resolve_channel_user",
]
