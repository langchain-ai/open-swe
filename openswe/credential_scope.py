"""Resolve personal integration access and PR authorship within saved thread scope."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from openswe.run_config import RunConfig
from openswe.source_context import SourceContext
from openswe.tasks.schemas import ThreadMetadata
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_reads import read_thread
from openswe.workspaces.store import WORKSPACES


async def _thread_scope(
    config: Mapping[str, Any] | None = None, *, thread_id: str | None = None
) -> tuple[RunConfig, dict[str, Any]]:
    cfg = RunConfig.from_config(config) if config is not None else RunConfig.from_runtime()
    thread_id = thread_id or cfg.thread_id
    if not thread_id:
        raise RuntimeError("Cannot resolve credential scope without a thread_id")
    thread = await read_thread(thread_id)
    metadata = thread_metadata(thread)
    visibility = metadata.get("visibility", "public")
    if visibility not in ("public", "private"):
        raise RuntimeError("Cannot resolve credentials for an unknown thread visibility")
    owner_type = metadata.get("owner_type")
    if owner_type not in (None, "user", "system"):
        raise RuntimeError("Cannot resolve credentials for an unknown thread owner type")
    if owner_type == "system" and visibility != "public":
        raise RuntimeError("System threads cannot use private credentials")
    return cfg, metadata


def private_owner_login(cfg: RunConfig, metadata: Mapping[str, Any]) -> str | None:
    """The run's actor when this is a private thread they own, else None."""
    if metadata.get("visibility", "public") == "public":
        return None
    owner = metadata.get("owner_login")
    login = ((cfg.sender_id or "") if not cfg.github_linked else cfg.github_login or "").strip()
    if not isinstance(owner, str) or not login or owner.strip().lower() != login.lower():
        return None
    return login


def _private_owner_login(cfg: RunConfig, metadata: Mapping[str, Any]) -> str:
    owner = metadata.get("owner_login")
    if not isinstance(owner, str) or not owner.strip():
        raise RuntimeError("Private thread has no credential owner")
    login = private_owner_login(cfg, metadata)
    if login is None:
        raise RuntimeError("Personal credentials require the private thread owner to start the run")
    return login


async def private_credential_login(
    config: Mapping[str, Any] | None = None, *, thread_id: str | None = None
) -> str | None:
    """Personal integrations require the saved private owner to start the run."""
    cfg, metadata = await _thread_scope(config, thread_id=thread_id)
    if not cfg.github_linked:
        if (
            not cfg.user_id
            or metadata.get("owner_login") != f"user:{cfg.user_id}"
            or metadata.get("visibility") != "private"
        ):
            raise PermissionError("Personal credentials require the private thread owner")
        return f"user:{cfg.user_id}"
    if metadata.get("visibility", "public") == "public":
        return None
    return _private_owner_login(cfg, metadata)


@dataclass(frozen=True)
class GitHubCredentialScope:
    """GitHub acts as the verified private owner, or as the workspace App."""

    login: str | None

    @property
    def kind(self) -> Literal["personal", "system"]:
        return "personal" if self.login is not None else "system"

    @classmethod
    async def is_public_ask(cls, config: Mapping[str, Any] | None = None) -> bool:
        _, metadata = await _thread_scope(config)
        slack = SourceContext.from_metadata(metadata).slack_thread
        return (
            metadata.get("visibility", "public") == "public"
            and slack is not None
            and bool(slack.channel_id)
            and await WORKSPACES.is_ask_channel(slack.channel_id)
        )

    @classmethod
    async def for_thread(
        cls, config: Mapping[str, object] | None = None, *, thread_id: str | None = None
    ) -> GitHubCredentialScope:
        return cls(await private_credential_login(config, thread_id=thread_id))


async def pr_author_login() -> str | None:
    """The GitHub account a PR is opened as: always the thread's owner.

    Private threads are pinned to their owner, system threads to the App, and a public
    Ask run is checked against its requester before the PR opens as the App.
    """
    cfg, metadata = await _thread_scope()
    if not cfg.github_linked:
        raise PermissionError("GitHub is not linked for this thread")
    if metadata.get("visibility", "public") == "private":
        return _private_owner_login(cfg, metadata)
    ask = await GitHubCredentialScope.is_public_ask()
    if ask and not cfg.background_task_completion:
        login = (cfg.github_login or "").strip()
        if not login:
            raise RuntimeError("Ask PR creation requires an authenticated requester")
        return login
    owner = metadata.get("owner_login")
    if metadata.get("owner_type") == "system" or not isinstance(owner, str) or not owner.strip():
        if ask:
            raise RuntimeError("Ask background completion requires a thread owner")
        if metadata.get("owner_type") == "user":
            raise RuntimeError("User-owned thread has no GitHub owner for PR creation")
        return None
    login = owner.strip()
    if metadata.get("owner_user_id") or login.startswith("user:"):
        user = await ThreadMetadata.model_validate(metadata).owner_if_any()
        login = user.github_login if user is not None else ""
    if not login:
        raise RuntimeError("The thread's owner has no linked GitHub account to open the PR as")
    return login
