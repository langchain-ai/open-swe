"""Layered Context Hub skills, served to agents as virtual files.

Skills are sourced from LangSmith Context Hub agent repos via deepagents'
``ContextHubBackend`` and mounted on a ``CompositeBackend`` so the agent reads
them with ``read_file`` (nothing is written to the execution sandbox).

Two layers, merged by ``SkillsMiddleware`` with last-wins-by-name precedence:

- **global** (``/skills/``) — shared defaults in ``OPENSWE_SKILLS_REPO``
  (default ``-/openswe-skills``), authored centrally in the LangSmith Hub UI.
- **user** (``/user-skills/``) — per-user repo derived from the resolved GitHub
  login (``<global>-<login>``). A user with no repo simply gets the defaults.

Skill routes are wrapped read-only (``ReadOnlyBackend``): authoring happens in
the Hub UI, so the agent's ``write_file``/``edit_file`` tools must not be able to
commit to the shared (or another user's) hub repo with the server's credentials.

The analyzer is a special case: its playbooks are read by an explicit
``read_file("/skills/<mode>/SKILL.md")`` in the system prompt, so it only uses
the hub when that repo actually contains both analyzer playbooks; otherwise it
falls back to the bundled SKILL.md floor (seeded into the ``files`` channel by
the launcher), preserving today's behaviour with zero regression.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.context_hub import ContextHubBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.state import StateBackend

from .analyzer_skills import ANALYZER_MODES, SKILLS_DIR, SKILLS_ROUTE

logger = logging.getLogger(__name__)

DEFAULT_GLOBAL_SKILLS_REPO = "-/openswe-skills"
USER_SKILLS_ROUTE = "/user-skills/"

_READ_ONLY_ERROR = "This skills location is read-only; author skills in the LangSmith Hub UI."

# Context Hub repo handles allow lowercase alphanumerics, hyphens and underscores.
_INVALID_HANDLE_CHARS = re.compile(r"[^a-z0-9_-]+")


class ReadOnlyBackend(BackendProtocol):
    """Wraps a backend so reads pass through but writes are rejected."""

    def __init__(self, inner: BackendProtocol) -> None:
        self.inner = inner

    # --- reads: delegate (sync + async) ---
    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self.inner.read(file_path, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self.inner.aread(file_path, offset, limit)

    def ls(self, path: str = "/") -> LsResult:
        return self.inner.ls(path)

    async def als(self, path: str = "/") -> LsResult:
        return await self.inner.als(path)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return self.inner.glob(pattern, path)

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return await self.inner.aglob(pattern, path)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return self.inner.grep(pattern, path, glob)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return await self.inner.agrep(pattern, path, glob)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.inner.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self.inner.adownload_files(paths)

    # --- writes: reject (sync + async) ---
    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_ERROR)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_ERROR)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=_READ_ONLY_ERROR) for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=_READ_ONLY_ERROR) for path, _ in files]


def global_skills_identifier() -> str | None:
    """Return the global skills hub repo, or ``None`` when disabled.

    Reads ``OPENSWE_SKILLS_REPO`` (default ``-/openswe-skills``); set it to an
    empty string to turn the global skills layer off entirely.
    """
    repo = os.getenv("OPENSWE_SKILLS_REPO", DEFAULT_GLOBAL_SKILLS_REPO).strip()
    return repo or None


def _sanitize_handle(login: str) -> str:
    handle = _INVALID_HANDLE_CHARS.sub("-", login.strip().lower()).strip("-")
    return handle


def user_skills_identifier(login: str | None) -> str | None:
    """Return the per-user skills hub repo for ``login``, or ``None``.

    Derived from the global repo as ``<global>-<sanitized-login>`` so a custom
    ``OPENSWE_SKILLS_REPO`` keeps user repos in the same namespace.
    """
    base = global_skills_identifier()
    if not base or not login:
        return None
    handle = _sanitize_handle(login)
    if not handle:
        return None
    return f"{base}-{handle}"


def _make_hub_backend(identifier: str) -> ContextHubBackend:
    # Indirection kept tiny so tests can monkeypatch hub construction.
    return ContextHubBackend(identifier)


def _analyzer_playbook_paths() -> list[str]:
    return [f"/{skill}/SKILL.md" for skill in ANALYZER_MODES.values()]


async def _hub_has_files(backend: BackendProtocol, paths: list[str]) -> bool:
    """Return True only if every path resolves to content in the hub repo."""
    try:
        responses = await asyncio.to_thread(backend.download_files, paths)
    except Exception:
        logger.warning("Context Hub skills repo unreachable; treating as empty", exc_info=True)
        return False
    return len(responses) == len(paths) and all(
        r.error is None and r.content is not None for r in responses
    )


def build_agent_skill_routes(
    login: str | None,
) -> tuple[dict[str, BackendProtocol], list[str]]:
    """Build ``CompositeBackend`` routes + ordered skill sources for the agent.

    Returns ``({route: backend}, [source, ...])``. Sources are ordered so the
    user layer wins on name collisions (``SkillsMiddleware`` is last-wins). Hub
    routes are read-only. No network is performed here; an empty/unreachable
    repo surfaces as a middleware load warning and is otherwise harmless.
    """
    routes: dict[str, BackendProtocol] = {}
    sources: list[str] = []

    global_repo = global_skills_identifier()
    if global_repo:
        routes[SKILLS_ROUTE] = ReadOnlyBackend(_make_hub_backend(global_repo))
        sources.append(SKILLS_ROUTE)

    user_repo = user_skills_identifier(login)
    if user_repo:
        routes[USER_SKILLS_ROUTE] = ReadOnlyBackend(_make_hub_backend(user_repo))
        sources.append(USER_SKILLS_ROUTE)  # last => higher priority

    return routes, sources


async def build_analyzer_skills_backend(
    default_backend: BackendProtocol,
) -> tuple[CompositeBackend, list[str]]:
    """Mount ``/skills/`` for the analyzer: hub global if it carries both
    analyzer playbooks, else the bundled ``StateBackend`` floor.

    The bundled floor is served from the ``files`` channel (seeded by the
    launcher), so the analyzer's explicit-read playbook path keeps working when
    the hub repo is empty, unreachable, or missing the analyzer playbooks.
    """
    skills_backend: BackendProtocol = StateBackend()

    global_repo = global_skills_identifier()
    if global_repo:
        hub = _make_hub_backend(global_repo)
        if await _hub_has_files(hub, _analyzer_playbook_paths()):
            skills_backend = hub

    backend = CompositeBackend(
        default=default_backend, routes={SKILLS_ROUTE: ReadOnlyBackend(skills_backend)}
    )
    return backend, [SKILLS_ROUTE]


def bundled_skill_files_for_hub() -> dict[str, str]:
    """Return ``{hub_path: content}`` for the bundled skills (for seeding)."""
    files: dict[str, str] = {}
    for skill in ANALYZER_MODES.values():
        skill_md = SKILLS_DIR / skill / "SKILL.md"
        files[f"{skill}/SKILL.md"] = skill_md.read_text(encoding="utf-8")
    return files
