"""Where a main-agent run executes, and everything that follows from it.

``get_agent`` resolves one of these once and then calls it unconditionally, so
the list of things local mode changes is the body of ``DesktopRunEnvironment``
rather than a dozen ``source == "desktop"`` branches spread through the factory.
"""

import asyncio
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from deepagents.backends import LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from deepagents.backends.state import StateBackend
from deepagents.backends.store import StoreBackend

from ..config import in_process_langgraph_client, is_langsmith_sandbox, local_projects_file
from ..middleware import PullRequestCreationGuardMiddleware
from ..runtime.sandbox import ensure_sandbox_for_thread, environment_slug, resolve_default_repo
from ..settings.agent_overrides import load_profile
from ..settings.options import default_model_pair
from ..settings.skills import ORGANIZATION_SKILLS_NAMESPACE, SKILLS_NAMESPACE
from ..settings.team_settings import (
    get_team_default_model_pair,
    get_team_default_thread_title_model,
    get_team_fable_enabled,
)
from ..tools import fetch_url, http_request, web_search
from ..utils import ttl_cache
from ..utils.read_only_backend import ReadOnlyBackend
from ..utils.thread_settings import ThreadSettings, load_thread_settings, store_thread_settings
from ._assembly import cached_gateway_enabled

USER_SKILLS_ROUTE = "/skills/"
ORGANIZATION_SKILLS_ROUTE = "/organization-skills/"
BUNDLED_SKILLS_ROUTE = "/bundled-skills/"
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "bundled_skills"

# Process-level environment handed to the desktop shell, not app configuration.
SHELL_ENV_KEYS = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TMPDIR")

ModelPair = tuple[str, str | None]


@dataclass(frozen=True)
class ModelDefaults:
    """Everything a run needs before per-thread and per-user overrides apply."""

    team: tuple[ModelPair, ModelPair]
    title: tuple[str, str]
    use_gateway: bool
    profile: dict[str, Any] | None
    fable_enabled: bool


# Team/profile settings are accepted stale for a short TTL so graph factories
# stay off the critical path during worker load and retry storms.
async def _cached_team_default_model_pair(kind: Literal["agent", "reviewer"]):
    return await ttl_cache.cached(
        f"team-default-model-pair:{kind}", 60, lambda: get_team_default_model_pair(kind)
    )


async def _cached_thread_title_model() -> tuple[str, str]:
    return await ttl_cache.cached(
        "team:thread-title-model", 60, get_team_default_thread_title_model
    )


async def _cached_fable_enabled() -> bool:
    return await ttl_cache.cached("team:fable-enabled", 60, get_team_fable_enabled)


async def cached_profile(login: str | None) -> dict[str, Any] | None:
    if not login:
        return None
    return await ttl_cache.cached(f"profile:{login}", 30, lambda: load_profile(login))


def _bundled_skills_route() -> tuple[str, BackendProtocol]:
    return BUNDLED_SKILLS_ROUTE, ReadOnlyBackend(
        FilesystemBackend(root_dir=BUNDLED_SKILLS_DIR, virtual_mode=True)
    )


class RunEnvironment(ABC):
    """The decisions that follow from *where* a run executes."""

    #: The run works in a developer's own checkout on their machine.
    local_workspace: bool
    #: The run may load the triggering user's credentialed integrations.
    credentialed_integrations: bool

    @staticmethod
    def for_run(thread_id: str, configurable: dict[str, Any]) -> "RunEnvironment":
        if configurable.get("source") == "desktop":
            return DesktopRunEnvironment(thread_id, configurable)
        return CloudRunEnvironment(thread_id, configurable)

    def __init__(self, thread_id: str, configurable: dict[str, Any]) -> None:
        self._thread_id = thread_id
        self._configurable = configurable

    @abstractmethod
    async def make_backend(self) -> SandboxBackendProtocol:
        """Connect (or create) the filesystem + shell this run works in."""

    @abstractmethod
    async def load_thread_settings(self) -> ThreadSettings:
        """The model/effort settings frozen onto this thread by its first run."""

    @abstractmethod
    async def store_thread_settings(self, settings: ThreadSettings) -> None: ...

    @abstractmethod
    async def load_profile(self, login: str | None) -> dict[str, Any] | None:
        """The dashboard profile whose preferences apply to ``login``."""

    @abstractmethod
    async def resolve_model_defaults(self, profile_login: str | None) -> ModelDefaults: ...

    @abstractmethod
    def static_tools(self, base: list[Any]) -> list[Any]:
        """The tool list this run gets, given the one the factory assembled."""

    @abstractmethod
    def skill_routes(
        self, profile_login: str | None
    ) -> tuple[dict[str, BackendProtocol], list[str]]:
        """Skill backends by route, and the routes to load them from, in order."""

    @abstractmethod
    def extra_middleware(self) -> list[Any]:
        """Middleware only this environment needs."""

    @abstractmethod
    def sandbox_file_downloads(self, *, stop_summary: bool) -> bool:
        """Whether signed sandbox download URLs can be minted for this run."""


class CloudRunEnvironment(RunEnvironment):
    """A hosted run: its own sandbox, the team's settings, the full tool set."""

    local_workspace = False
    credentialed_integrations = True

    async def make_backend(self) -> SandboxBackendProtocol:
        return await ensure_sandbox_for_thread(
            self._thread_id,
            repo=await resolve_default_repo(self._configurable),
            environment_slug=environment_slug(self._configurable),
        )

    async def load_thread_settings(self) -> ThreadSettings:
        return await load_thread_settings(in_process_langgraph_client(), self._thread_id)

    async def store_thread_settings(self, settings: ThreadSettings) -> None:
        await store_thread_settings(in_process_langgraph_client(), self._thread_id, settings)

    async def load_profile(self, login: str | None) -> dict[str, Any] | None:
        return await cached_profile(login)

    async def resolve_model_defaults(self, profile_login: str | None) -> ModelDefaults:
        team, title, use_gateway, profile, fable_enabled = await asyncio.gather(
            _cached_team_default_model_pair("agent"),
            _cached_thread_title_model(),
            cached_gateway_enabled(),
            cached_profile(profile_login),
            _cached_fable_enabled(),
        )
        return ModelDefaults(team, title, use_gateway, profile, fable_enabled)

    def static_tools(self, base: list[Any]) -> list[Any]:
        return base

    def skill_routes(
        self, profile_login: str | None
    ) -> tuple[dict[str, BackendProtocol], list[str]]:
        routes: dict[str, BackendProtocol] = dict([_bundled_skills_route()])
        routes[ORGANIZATION_SKILLS_ROUTE] = ReadOnlyBackend(
            StoreBackend(namespace=lambda _runtime: (ORGANIZATION_SKILLS_NAMESPACE,))
        )
        sources = [ORGANIZATION_SKILLS_ROUTE, BUNDLED_SKILLS_ROUTE]
        if profile_login:
            routes[USER_SKILLS_ROUTE] = ReadOnlyBackend(
                StoreBackend(
                    namespace=lambda _runtime, login=profile_login: (SKILLS_NAMESPACE, login)
                )
            )
            sources.insert(0, USER_SKILLS_ROUTE)
        return routes, sources

    def extra_middleware(self) -> list[Any]:
        return [PullRequestCreationGuardMiddleware()]

    def sandbox_file_downloads(self, *, stop_summary: bool) -> bool:
        return is_langsmith_sandbox() and not stop_summary


class DesktopRunEnvironment(RunEnvironment):
    """A run against a developer's own checkout, driven from the desktop app.

    Everything below is what local mode changes, and why:

    - the backend is a shell on the developer's machine, restricted to an
      allow-listed project directory, instead of a cloud sandbox;
    - thread settings, the team defaults, the gateway and the dashboard profile
      are all skipped — a local run has no workspace to read them from, so it
      takes the built-in default model and talks to the provider directly;
    - settings are never persisted back, for the same reason;
    - the tool set is the read-only research trio: everything else is either
      hosted-only (Slack, Linear, PR creation, sandbox management) or
      credentialed against the team's workspace;
    - user skills come from the run's own state, seeded by the desktop app,
      rather than from the workspace store;
    - ``PullRequestCreationGuardMiddleware`` is dropped: it blocks ``gh pr
      create`` in favour of the hosted ``open_pull_request`` tool, which this run
      does not have — the prompt tells it to open PRs with ``gh`` under the
      developer's own GitHub identity;
    - signed sandbox download URLs cannot be minted for files that are already
      on the user's own disk.
    """

    local_workspace = True
    credentialed_integrations = False

    async def make_backend(self) -> SandboxBackendProtocol:
        return LocalShellBackend(
            root_dir=resolve_desktop_project(self._configurable),
            virtual_mode=True,
            env={key: value for key in SHELL_ENV_KEYS if (value := os.environ.get(key))},
        )

    async def load_thread_settings(self) -> ThreadSettings:
        return {}

    async def store_thread_settings(self, settings: ThreadSettings) -> None:
        return None

    async def load_profile(self, login: str | None) -> dict[str, Any] | None:
        return None

    async def resolve_model_defaults(self, profile_login: str | None) -> ModelDefaults:
        team_default = default_model_pair()
        return ModelDefaults(
            team=(team_default, team_default),
            title=team_default,
            use_gateway=False,
            profile=None,
            fable_enabled=False,
        )

    def static_tools(self, base: list[Any]) -> list[Any]:
        return [http_request, fetch_url, web_search]

    def skill_routes(
        self, profile_login: str | None
    ) -> tuple[dict[str, BackendProtocol], list[str]]:
        routes: dict[str, BackendProtocol] = dict([_bundled_skills_route()])
        routes[USER_SKILLS_ROUTE] = ReadOnlyBackend(StateBackend())
        return routes, [USER_SKILLS_ROUTE, BUNDLED_SKILLS_ROUTE]

    def extra_middleware(self) -> list[Any]:
        return []

    def sandbox_file_downloads(self, *, stop_summary: bool) -> bool:
        return False


def resolve_desktop_project(configurable: dict[str, Any]) -> str:
    """The allow-listed project directory a desktop run may open."""
    requested = configurable.get("local_project_path")
    allowlist_path = local_projects_file()
    if not isinstance(requested, str) or not requested or not allowlist_path:
        raise ValueError("Desktop runs require an allowlisted local_project_path")
    with open(allowlist_path, encoding="utf-8") as file:
        entries = json.load(file)
    if not isinstance(entries, list):
        raise ValueError("OPEN_SWE_LOCAL_PROJECTS_FILE must contain a JSON array")
    allowed = {
        os.path.realpath(entry["cwd"] if isinstance(entry, dict) else entry)
        for entry in entries
        if isinstance(entry, str) or (isinstance(entry, dict) and isinstance(entry.get("cwd"), str))
    }
    project = os.path.realpath(requested)
    if project not in allowed or not Path(project).is_dir():
        raise ValueError("local_project_path is not an allowed project directory")
    return project
