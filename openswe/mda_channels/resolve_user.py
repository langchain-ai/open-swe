"""Who a channel's run is for: the MDA deployment asks before every run a channel starts.

``mda/identity.py`` names this endpoint as the deployment's ``resolve_user``. MDA
posts the sender each channel verified, Slack's, GitHub's or Linear's, and runs
the delivery as the Open SWE user it is answered with: the principal a trusted
backend call for that user id gets. So one person is one principal on every
channel, and the dashboard's runs can share it.

The sender maps to a user the way every other ingress maps one
(``User.for_person``): a linked Slack account, a GitHub account by its numeric
id, and Linear by email. Anyone else keeps the channel's own principal (``null``):
the channel endpoints still gate the run and ask the person to connect their
account, as before.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from managed_deepagents import ResolveUserEndpointRequest
from typing_extensions import TypedDict

from openswe.input_messages import PersonIdentity
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
    """The Open SWE user a channel's verified sender is, or ``None`` for the channel's default."""
    actor = call.get("actor")
    if actor is None:
        # The channel couldn't verify who sent it; the channel's principal runs it.
        return None
    team_id: str | None = None
    person: PersonIdentity
    if actor["provider"] == "slack":
        person = {"id": f"slack:{actor['id']}"}
        team_id = actor["teamId"]
    elif actor["provider"] == "github":
        if actor["type"] != "User":
            # Bots and organizations run as the channel.
            return None
        person = {"id": f"github:{actor['id']}"}
    else:
        email = actor.get("email", "")
        if not email:
            return None
        person = {"id": f"linear:{actor['id']}", "email": email}
    user = await User.for_person(person)
    if user is None or (team_id is not None and not _same_slack_team(user, actor["id"], team_id)):
        logger.info(
            "Channel sender maps to no Open SWE user", extra={"channel_provider": actor["provider"]}
        )
        return None
    return {"type": "user", "id": str(user.id)}


def _same_slack_team(user: User, slack_user_id: str, team_id: str) -> bool:
    """Whether the linked Slack account is in the sender's workspace, when both name one."""
    linked = next(
        (i for i in user.identities if i.provider == "slack" and i.external_id == slack_user_id),
        None,
    )
    return linked is None or not linked.team_id or linked.team_id == team_id
