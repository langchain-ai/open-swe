"""PostgreSQL file overlap evidence from successful edits and PR webhooks."""

import json
import logging
from datetime import datetime
from pathlib import PurePosixPath
from uuid import uuid7

from runtime_api.edit_conflicts import EditConflicts, EditedFile
from sqlalchemy import text

from openswe.config import ENV
from openswe.database import postgres
from openswe.github.http import GitHubClient
from openswe.github.pull_requests import PullRequestEvent, PullRequestPayload
from openswe.github.token_scope import token_repositories_from_metadata
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.threads.store import THREADS
from openswe.threads.summary import thread_is_readable
from openswe.users import User
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)


class ChangedPull(PullRequestPayload):
    updated_at: datetime


class FileChangeEvent(PullRequestEvent):
    pull_request: ChangedPull
    action: str

    async def refresh(self) -> None:
        """Refresh one PR head, serialized across replicas and duplicate deliveries."""
        pull = self.to_pull_request()
        if pull is None or self.action not in {"opened", "reopened", "synchronize", "closed"}:
            return
        async with postgres.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"edit-conflicts:{pull.url}"},
            )
            cached = (
                (
                    await session.execute(
                        text("""SELECT c.* FROM pull_request_file_cache c
                            JOIN pull_request p ON p.id = c.pull_request_id
                            JOIN repository r ON r.id = p.repository_id
                            WHERE r.key = :repo AND p.number = :number"""),
                        {"repo": self.repo_full_name.lower(), "number": pull.number},
                    )
                )
                .mappings()
                .first()
            )
            updated = self.pull_request.updated_at
            if cached is not None and cached["source_updated_at"] > updated:
                return
            pull = await pull.save(repository_private=self.repo_private)
            active = self.action != "closed"
            head = self.pull_request.head_sha
            if active and not head:
                return
            paths: list[str] = []
            if active and cached is not None and cached["active"] and cached["head_sha"] == head:
                paths = cached["paths"]
            elif active:
                async with GitHubClient.as_app(pull.owner, pull.repo) as github:
                    files = (
                        await github.repo(pull.owner, pull.repo)
                        .pull_request(pull.number)
                        .files(max_pages=30, count=self.pull_request.changed_files)
                    )
                paths = sorted(
                    {
                        path
                        for file in files
                        for key in ("filename", "previous_filename")
                        if isinstance(path := file.get(key), str)
                    }
                )
            await session.execute(
                text("""
                    INSERT INTO pull_request_file_cache
                        (id, pull_request_id, head_sha, paths, active, source_updated_at)
                    VALUES (:id, :pull, :head, CAST(:paths AS jsonb), :active, :updated)
                    ON CONFLICT (pull_request_id) DO UPDATE SET
                        head_sha = excluded.head_sha, paths = excluded.paths,
                        active = excluded.active, source_updated_at = excluded.source_updated_at,
                        updated_at = clock_timestamp()
                """),
                {
                    "id": uuid7(),
                    "pull": pull.id,
                    "head": head,
                    "paths": json.dumps(paths),
                    "active": active,
                    "updated": updated,
                },
            )

    @classmethod
    async def receive(cls, payload: object) -> None:
        """A cache failure must not prevent the webhook's existing consumers."""
        try:
            event = cls.parse(payload)
            if isinstance(event, cls):
                await event.refresh()
        except Exception:
            logger.warning("Could not refresh PR file overlap cache", exc_info=True)


class EditOverlap:
    def __init__(self, config: RunConfig) -> None:
        self.config = config

    async def check(self, edit: EditedFile) -> EditConflicts:
        """Record one successful edit and read overlaps without calling GitHub."""
        cfg = self.config
        if not cfg.thread_id or not postgres.configured():
            return EditConflicts()
        path = PurePosixPath(edit.path)
        if path.is_absolute() or ".." in path.parts or str(path) == ".":
            return EditConflicts()
        current = await THREADS.get(cfg.thread_id)
        if current is None:
            return EditConflicts()
        scope = token_repositories_from_metadata(current.metadata)
        repository = edit.repository.lower()
        if scope is not None and repository not in {repo.lower() for repo in scope}:
            return EditConflicts()
        workspace = await WORKSPACES.get(cfg.workspace or "default")
        allowed = {repo.lower() for repo in workspace.repos} if workspace else set()
        if cfg.repo:
            allowed.add(cfg.repo.full_name.lower())
        if repository not in allowed:
            return EditConflicts()
        actor = await User.for_login("github", cfg.github_login) if cfg.github_login else None
        async with postgres.session() as session:
            repository_id = await session.scalar(
                text("SELECT id FROM repository WHERE key = :repo"), {"repo": repository}
            )
            if repository_id is None:
                return EditConflicts()
            await session.execute(
                text("""
                    INSERT INTO session_file_edit
                        (id, repository_id, thread_id, path, branch, created_by, updated_by)
                    VALUES (:id, :repo, :thread, :path, :branch, :actor, :actor)
                    ON CONFLICT (repository_id, thread_id, path) DO UPDATE SET
                        branch = excluded.branch, updated_by = excluded.updated_by,
                        updated_at = clock_timestamp()
                """),
                {
                    "id": uuid7(),
                    "repo": repository_id,
                    "thread": cfg.thread_id,
                    "path": str(path),
                    "branch": edit.branch,
                    "actor": actor.id if actor else None,
                },
            )
            sessions = (
                (
                    await session.execute(
                        text("""
                        SELECT e.thread_id, t.metadata FROM session_file_edit e
                        JOIN thread_index t ON t.thread_id = e.thread_id
                        WHERE e.repository_id = :repo AND e.path = :path
                          AND e.thread_id != :thread
                          AND e.updated_at > clock_timestamp() - interval '24 hours'
                          AND coalesce(t.metadata->>'resolved', 'false') != 'true'
                          AND coalesce(t.metadata->>'workspace', 'default') = :workspace
                        ORDER BY e.updated_at DESC LIMIT 50
                    """),
                        {
                            "repo": repository_id,
                            "path": str(path),
                            "thread": cfg.thread_id,
                            "workspace": cfg.workspace or "default",
                        },
                    )
                )
                .mappings()
                .all()
            )
            prs = (
                (
                    await session.execute(
                        text("""
                        SELECT p.owner, p.repo, p.number FROM pull_request_file_cache c
                        JOIN pull_request p ON p.id = c.pull_request_id
                        WHERE p.repository_id = :repo AND c.active
                          AND c.paths @> CAST(:path AS jsonb)
                          AND (:branch = '' OR p.head_ref != :branch)
                          AND NOT EXISTS (SELECT 1 FROM pull_request_thread l
                              WHERE l.pull_request_id = p.id AND l.thread_id = :thread)
                        ORDER BY c.updated_at DESC LIMIT 8
                    """),
                        {
                            "repo": repository_id,
                            "path": json.dumps([str(path)]),
                            "branch": edit.branch,
                            "thread": cfg.thread_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
        links = [f"https://github.com/{pr['owner']}/{pr['repo']}/pull/{pr['number']}" for pr in prs]
        for other in sessions:
            metadata = other["metadata"]
            if metadata.get("visibility", "public") != "public" and (
                current.metadata.get("visibility", "public") == "public"
                or metadata.get("owner_login") != current.metadata.get("owner_login")
            ):
                continue
            if thread_is_readable(metadata, cfg.github_login, cfg.user_email):
                links.append(
                    f"{ENV.DASHBOARD_BASE_URL.get().rstrip('/')}/agents/{other['thread_id']}"
                )
        if not links:
            return EditConflicts()
        return EditConflicts(
            reminder=prompt(
                "middleware/edit_conflicts",
                repository=repository,
                path=json.dumps(str(path)),
                links="\n".join(f"- {link}" for link in links[:8]),
            )
        )
