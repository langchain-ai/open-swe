"""Public participant summaries for an authorized dashboard thread."""

import asyncio
from collections.abc import Mapping

from pydantic import BaseModel

from openswe.slack.users import SlackUser
from openswe.source_context import SourceContext
from openswe.threads.access import _readable_thread_metadata
from openswe.users import User
from openswe.users.avatars import slack_profile
from openswe.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    participant_logins,
)


class ThreadParticipant(BaseModel):
    id: str
    displayName: str
    githubLogin: str | None = None
    avatarUrl: str = ""


async def _participant(user: User) -> ThreadParticipant:
    profile = await slack_profile(user)
    return ThreadParticipant(
        id=str(user.id),
        displayName=(profile.display_name if profile else "")
        or user.display_name
        or user.github_login
        or "Participant",
        githubLogin=user.github_login or None,
        avatarUrl=profile.avatar_url if profile else "",
    )


async def participant_summaries(metadata: Mapping[str, object]) -> list[ThreadParticipant]:
    logins = set(participant_logins(metadata.get(PARTICIPANT_LOGINS_KEY)))
    owner = metadata.get("owner_login")
    if isinstance(owner, str) and owner:
        logins.add(owner.lower())
    emails = participant_logins(metadata.get(PARTICIPANT_EMAILS_KEY))
    users = await asyncio.gather(
        *(User.for_login("github", login) for login in sorted(logins)),
        *(User.for_email(email) for email in emails),
    )
    stored_slack_ids = metadata.get("participant_slack_ids")
    slack_ids = (
        {value for value in stored_slack_ids if isinstance(value, str) and value}
        if isinstance(stored_slack_ids, list)
        else set()
    )
    slack = SourceContext.from_metadata(metadata).slack_thread
    if slack and slack.triggering_user_id and not slack.triggering_bot_id:
        slack_ids.add(slack.triggering_user_id)
    slack_users = await asyncio.gather(
        *(User.for_identity("slack", user_id) for user_id in sorted(slack_ids))
    )
    users.extend(slack_users)
    unique = {user.id: user for user in users if user is not None}
    participants = list(await asyncio.gather(*(_participant(user) for user in unique.values())))
    unknown_slack_ids = [
        user_id
        for user_id, user in zip(sorted(slack_ids), slack_users, strict=True)
        if user is None
    ]
    profiles = await asyncio.gather(*(SlackUser.load(user_id) for user_id in unknown_slack_ids))
    participants.extend(
        ThreadParticipant(
            id=f"slack:{user_id}",
            displayName=(profile.display_name if profile else "") or user_id,
            avatarUrl=profile.avatar_url if profile else "",
        )
        for user_id, profile in zip(unknown_slack_ids, profiles, strict=True)
    )
    known_logins = {user.github_login.lower() for user in unique.values()}
    participants.extend(
        ThreadParticipant(id=f"github:{login}", displayName=login, githubLogin=login)
        for login in sorted(logins - known_logins)
    )
    return sorted(participants, key=lambda person: (person.displayName.lower(), person.id))


async def get_thread_participants(
    thread_id: str, login: str, *, email: str | None = None
) -> list[ThreadParticipant]:
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    return await participant_summaries(metadata)
