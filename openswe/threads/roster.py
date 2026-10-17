"""Who is in a thread, each person described from the id they are known by."""

import asyncio
import logging
from dataclasses import dataclass, replace
from uuid import UUID

from langgraph_sdk.client import LangGraphClient

from openswe.dashboard.agent_overrides import load_profile, profile_draft_prs
from openswe.dashboard.user_instructions import get_user_custom_instructions
from openswe.run_config import RunConfig
from openswe.tools.admin_gate import participant_is_admin
from openswe.users import User
from openswe.utils.authorship import CollaboratorIdentity, ThreadParticipant
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_participants import PARTICIPANT_LOGINS_KEY, participant_logins
from openswe.utils.thread_reads import read_thread

logger = logging.getLogger(__name__)


class UnidentifiedSenderError(RuntimeError):
    """A human sender that neither the ``users`` table nor the run's surface identifies."""

    def __init__(self, person_id: str) -> None:
        super().__init__(f"Cannot identify the sender {person_id!r}; refusing to run for them")
        self.person_id = person_id


@dataclass(frozen=True)
class ThreadRoster:
    """The people of one run's thread, as the agent is told about them.

    Edges key everyone with an account on ``user:<uuid>``, so that row is the only
    source for them. A surface id names someone without one: the run's surface
    data describes them only when it belongs to that same id, and otherwise the
    surface that delivered their message has already introduced them.
    """

    thread_id: str
    cfg: RunConfig
    introduced: frozenset[str]
    client: LangGraphClient | None = None

    async def participants(self, sender_id: str) -> list[ThreadParticipant]:
        """The sender first, then everyone else who has posted in the thread."""
        sender, logins = await asyncio.gather(self.sender(sender_id), self._logins())
        sender_login = sender.identity.github_login.lower() if sender is not None else ""
        others = await asyncio.gather(
            *(self._for_login(login) for login in logins if login.lower() != sender_login)
        )
        return [*([sender] if sender is not None else []), *others]

    async def sender(self, person_id: str) -> ThreadParticipant | None:
        """The person ``person_id`` names, or ``None`` when there is nothing to add.

        That is a system principal, or a person a surface already introduced.
        Raises :class:`UnidentifiedSenderError` for a human nothing identifies.
        """
        platform, _, external_id = person_id.partition(":")
        slack = self.cfg.slack_thread
        if platform == "system":
            return None
        if platform == "user":
            try:
                user_id = UUID(external_id)
            except ValueError as exc:
                raise UnidentifiedSenderError(person_id) from exc
            user = await User.get(user_id)
            if user is None:
                raise UnidentifiedSenderError(person_id)
            on_slack = (
                slack is not None
                and bool(user.slack_user_id)
                and slack.triggering_user_id == user.slack_user_id
            )
            timezone = slack.triggering_user_timezone if slack is not None and on_slack else ""
            return await self._for_user(user, person_id, timezone=timezone)
        if platform == "github" and external_id.lower() == (self.cfg.github_login or "").lower():
            return replace(await self._for_login(external_id), person_id=person_id)
        if platform == "slack" and slack is not None and slack.triggering_user_id == external_id:
            return ThreadParticipant(
                identity=CollaboratorIdentity(
                    display_name=slack.triggering_user_name, commit_name="", commit_email=""
                ),
                person_id=person_id,
                timezone=slack.triggering_user_timezone,
                slack_user_id=external_id,
            )
        linear = self.cfg.linear_issue
        if (
            platform == "linear"
            and linear is not None
            and external_id.lower() == (self.cfg.user_email or "").lower()
        ):
            name = (linear.model_extra or {}).get("triggering_user_name")
            return ThreadParticipant(
                identity=CollaboratorIdentity(
                    display_name=name if isinstance(name, str) else "",
                    commit_name="",
                    commit_email="",
                ),
                person_id=person_id,
                email=external_id,
            )
        if platform in {"github", "slack", "linear"} and person_id in self.introduced:
            return None
        raise UnidentifiedSenderError(person_id)

    async def _logins(self) -> list[str]:
        try:
            thread = await read_thread(self.thread_id, self.client)
        except Exception:
            logger.warning("Could not read the thread's participants", exc_info=True)
            return []
        return participant_logins(thread_metadata(thread).get(PARTICIPANT_LOGINS_KEY))

    async def _for_login(self, login: str) -> ThreadParticipant:
        user = await User.for_login("github", login)
        if user is not None:
            return await self._for_user(user, f"user:{user.id}")
        return await self._for_github(login, "", f"github:{login}")

    async def _for_user(
        self, user: User, person_id: str, *, timezone: str = ""
    ) -> ThreadParticipant:
        login = user.github_login
        if not login:
            return ThreadParticipant(
                identity=CollaboratorIdentity(
                    display_name=user.display_name, commit_name="", commit_email=""
                ),
                person_id=person_id,
                email=user.email,
                timezone=timezone,
                linked=True,
                slack_user_id=user.slack_user_id,
            )
        return replace(
            await self._for_github(login, user.display_name, person_id),
            email=user.email,
            timezone=timezone,
            linked=True,
            slack_user_id=user.slack_user_id,
        )

    async def _for_github(self, login: str, display_name: str, person_id: str) -> ThreadParticipant:
        profile, workspace_admin, instructions = await asyncio.gather(
            load_profile(login),
            participant_is_admin(login),
            self._instructions(login),
        )
        name = display_name or login
        identity = CollaboratorIdentity(
            display_name=name, commit_name=name, commit_email="", github_login=login
        )
        return ThreadParticipant(
            identity=replace(identity, commit_email=identity.login_noreply_email),
            person_id=person_id,
            workspace_admin=workspace_admin,
            draft_prs=profile_draft_prs(profile),
            instructions=instructions,
        )

    async def _instructions(self, login: str) -> str:
        try:
            return await get_user_custom_instructions(login) or ""
        except Exception:
            logger.warning("Could not load a participant's instructions", exc_info=True)
            return ""
