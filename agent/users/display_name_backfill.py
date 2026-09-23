"""Fill in the display names the legacy import never had.

The legacy ``user_mappings`` import created users with only a GitHub login and
a Slack identity, so most rows pre-dating dashboard sign in carry an empty
``display_name``. Every Slack request already resolves the sender's profile
name for the prompt context; this persists it, once, when the table does not
have a name yet. A name from the dashboard GitHub sign in is the person's own
claim on their account and is never overwritten.
"""

import logging

from sqlalchemy import select, update

from agent.database import postgres
from agent.users.models import User, UserIdentity

logger = logging.getLogger(__name__)


async def persist_display_name(slack_user_id: str, name: str) -> None:
    """Give the person behind ``slack_user_id`` their Slack name, if they are nameless.

    The rename is conditional on the stored name still being empty inside the
    same UPDATE, so a dashboard sign in that lands between the Slack lookup and
    this write can never lose the GitHub name it claimed — and the first
    backfill wins for each person. An empty or ``unknown`` Slack name writes
    nothing.
    """
    name = name.strip()
    if not name or name == "unknown" or not slack_user_id:
        return
    if postgres.uri() is None:
        # The Store carries no users in this deployment, so there is nothing to
        # backfill; the Slack request continues without it.
        return
    async with postgres.session() as session:
        changed = await session.scalars(
            update(User)
            .where(
                User.display_name == "",
                User.id.in_(
                    select(UserIdentity.user_id).where(
                        UserIdentity.provider == "slack",
                        UserIdentity.external_id == slack_user_id,
                    )
                ),
            )
            .values(display_name=name)
            .returning(User.id)
        )
        await session.flush()
    if changed.first():
        logger.info(
            "Backfilled a display name from Slack",
            extra={"slack_user_id": slack_user_id},
        )
