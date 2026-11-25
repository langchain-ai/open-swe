"""Personal skill authority from the saved private thread, never caller-supplied context."""

import logging
from collections.abc import Mapping
from typing import Self
from uuid import UUID

from runtime_api.skills import RunSkills

from openswe.credential_scope import private_owner_login
from openswe.run_config import RunConfig
from openswe.skill_store.store import HubSkills
from openswe.source_context import SourceContext
from openswe.trace_metadata import RunOrigin
from openswe.users.models import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_reads import read_thread

logger = logging.getLogger(__name__)


class RunSkillStores:
    def __init__(self, owner: UUID | None) -> None:
        self.owner = owner

    @classmethod
    async def for_run(cls, config: Mapping[str, object]) -> Self:
        try:
            cfg = RunConfig.from_config(config)
            if (
                cfg.unattended
                or cfg.origin
                not in {
                    None,
                    RunOrigin.DASHBOARD,
                    RunOrigin.SLACK,
                    RunOrigin.CLAUDE_CODE,
                    RunOrigin.API,
                }
                or cfg.source
                not in {
                    None,
                    "dashboard",
                    "slack",
                    "web",
                    "claude_code",
                    "api",
                    "cli",
                    "mcp",
                    "desktop",
                }
                or not cfg.thread_id
            ):
                return cls(None)
            metadata = thread_metadata(await read_thread(cfg.thread_id))
            slack = SourceContext.from_metadata(metadata).slack_thread
            if (
                metadata.get("visibility") != "private"
                or metadata.get("owner_type") not in (None, "user")
                or metadata.get("schedule_id")
                or metadata.get("source") in {"schedule", "github", "linear", "incidents_agent"}
                or (slack is not None and not slack.is_dm)
                or (cfg.slack_thread is not None and not cfg.slack_thread.is_dm)
            ):
                return cls(None)
            login = (
                private_owner_login(cfg, metadata)
                if cfg.github_linked
                else f"user:{cfg.user_id}"
                if cfg.user_id and metadata.get("owner_login") == f"user:{cfg.user_id}"
                else None
            )
            if not login:
                return cls(None)
            user = (
                await User.get(UUID(login.removeprefix("user:")))
                if login.startswith("user:")
                else await User.for_login("github", login)
            )
            if user is None or (cfg.user_id is not None and str(user.id) != cfg.user_id):
                return cls(None)
            return cls(user.id)
        except Exception:
            logger.exception("Cannot resolve thread scope; omitting personal skills")
            return cls(None)

    async def listed(self) -> RunSkills:
        return RunSkills(
            organization_repo=HubSkills.repo(),
            personal_repo=None if self.owner is None else HubSkills.repo(self.owner),
        )
