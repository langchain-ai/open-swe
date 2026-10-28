"""Who a channel's run is for: the MDA deployment asks before every run a channel starts.

``mda/identity.py`` names this endpoint as the deployment's ``resolve_user``. MDA
posts the sender each channel verified, Slack's, GitHub's or Linear's, and runs
the delivery as the Open SWE user it is answered with: the principal a trusted
backend call for that user id gets. So one person is one principal on every
channel, and the dashboard's runs can share it.

The sender maps to a user the way every other ingress maps one
(``User.for_person``): a Slack account linked in the sender's own workspace, a
GitHub user by their numeric id, and a Linear user by email. Anyone else is
answered ``null``: MDA then runs a verified person as their own GitHub, Linear
or Slack principal, which owns no user connections, and a bot, an app or a
sender it couldn't verify as the channel. The channel endpoints still gate the
run and ask the person to connect their account, as before.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from managed_deepagents import ResolveUserEndpointRequest
from managed_deepagents._channels.actor import EndpointActor
from typing_extensions import TypedDict

from openswe.mda_channels.auth import require_mda_channel
from openswe.users import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mda", dependencies=[Depends(require_mda_channel)])


class AppUser(TypedDict):
    """MDA's ``ResolvedAppUser``: run as this Open SWE user. The only answer besides ``None``."""

    type: Literal["user"]
    id: str


@router.post("/resolve-user")
async def resolve_user(call: ResolveUserEndpointRequest) -> AppUser | None:
    """The Open SWE user a channel's verified sender is, or ``None`` for MDA's default."""
    actor = call.get("actor")
    if actor is None:
        # The channel couldn't verify who sent it; the channel's principal runs it.
        return None
    extra = {"channel_provider": actor["provider"]}
    try:
        user = await _open_swe_user(actor)
    except Exception:  # noqa: BLE001 - an error would fail the delivery; null still runs it
        logger.exception("Could not map a channel sender to an Open SWE user", extra=extra)
        return None
    if user is None:
        logger.info("Channel sender maps to no Open SWE user", extra=extra)
        return None
    return {"type": "user", "id": str(user.id)}


async def _open_swe_user(actor: EndpointActor) -> User | None:
    """The Open SWE user a verified person is; ``None`` for anyone else, on any channel."""
    if actor["provider"] == "slack":
        user = await User.for_person({"id": f"slack:{actor['id']}"})
        same_team = user is not None and _same_slack_team(user, actor["id"], actor["teamId"])
        return user if same_team else None
    if actor["provider"] == "github":
        # Bots and organizations run as the channel.
        if actor["type"] != "User":
            return None
        return await User.for_person({"id": f"github:{actor['id']}"})
    if actor["provider"] == "linear":
        # A session's creator or prompting user comes without a type; apps run as the channel.
        kind = actor.get("type")
        email = actor.get("email", "")
        if (kind is not None and kind.lower() != "user") or not email:
            return None
        return await User.for_person({"id": f"linear:{actor['id']}", "email": email})
    return None


def _same_slack_team(user: User, slack_user_id: str, team_id: str) -> bool:
    """Whether ``user``'s linked Slack account is the sender's, in the sender's workspace.

    A link that names no workspace, as older ones don't, is no one's: the same Slack
    user id may be someone else in another workspace.
    """
    return bool(team_id) and any(
        identity.provider == "slack"
        and identity.external_id == slack_user_id
        and identity.team_id == team_id
        for identity in user.identities
    )
