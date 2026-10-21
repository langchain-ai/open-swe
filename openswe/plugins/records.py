"""Installed plugins, their bundles, documents and threads in PostgreSQL."""

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from pydantic import ValidationError
from sqlalchemy import ColumnElement, ForeignKey, Text, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, aggregate_order_by, array, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.repo_names import RepoFullName
from openswe.plugins.manifest import PluginManifest
from openswe.plugins.source import PluginPackage, PluginSource
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class PluginRow(Base):
    __tablename__ = "plugin"

    repository: Mapped[str]
    path: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginBundleRow(Base):
    __tablename__ = "plugin_bundle"

    sha256: Mapped[str]
    html: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginInstallationRow(Base):
    __tablename__ = "plugin_installation"

    plugin: Mapped[UUID] = mapped_column(ForeignKey("plugin.id"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    ref: Mapped[str]
    commit_sha: Mapped[str]
    manifest: Mapped[JsonObject] = mapped_column(JSONB)
    bundle: Mapped[UUID] = mapped_column(ForeignKey("plugin_bundle.id"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginRepositoryGrantRow(Base):
    __tablename__ = "plugin_repository_grant"

    installation: Mapped[UUID] = mapped_column(
        ForeignKey("plugin_installation.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    repository: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginDocumentRow(Base):
    __tablename__ = "plugin_document"

    plugin: Mapped[UUID] = mapped_column(ForeignKey("plugin.id", ondelete="CASCADE"))
    collection: Mapped[str]
    key: Mapped[str]
    repository: Mapped[str | None]
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    document: Mapped[JsonObject] = mapped_column(JSONB)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginThreadRow(Base):
    __tablename__ = "plugin_thread"

    installation: Mapped[UUID] = mapped_column(
        ForeignKey("plugin_installation.id", ondelete="CASCADE")
    )
    thread_id: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginNameTaken(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"A plugin named {name!r} is already installed")


class InstallationNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("plugin not installed")


@dataclass(frozen=True, slots=True)
class Installation:
    """One install of a plugin, for one person or (``user_id`` ``None``) everyone, as one person sees it.

    ``repositories`` are the ones that person shared with it.
    """

    id: UUID
    plugin: UUID
    user_id: UUID | None
    source: PluginSource
    commit_sha: str
    manifest: PluginManifest
    repositories: tuple[str, ...]
    created_at: datetime | None

    @property
    def scope(self) -> Literal["user", "organization"]:
        return "organization" if self.user_id is None else "user"


def _granted_by(user_id: UUID) -> ColumnElement[list[str]]:
    return func.coalesce(
        select(
            func.array_agg(
                aggregate_order_by(
                    PluginRepositoryGrantRow.repository, PluginRepositoryGrantRow.repository
                )
            )
        )
        .where(
            PluginRepositoryGrantRow.installation == PluginInstallationRow.id,
            PluginRepositoryGrantRow.user_id == user_id,
        )
        .scalar_subquery(),
        cast(array([], type_=Text), ARRAY(Text)),
    )


def _installation(
    row: PluginInstallationRow, repository: str, path: str, repositories: Sequence[str]
) -> Installation:
    return Installation(
        id=row.id,
        plugin=row.plugin,
        user_id=row.user_id,
        source=PluginSource(RepoFullName(repository), path, row.ref),
        commit_sha=row.commit_sha,
        manifest=PluginManifest.model_validate(row.manifest),
        repositories=tuple(repositories),
        created_at=row.created_at,
    )


class PluginInstallations:
    """Every installed plugin."""

    @staticmethod
    async def visible_to(user_id: UUID) -> list[Installation]:
        """The person's own installs and everyone's; an install for everyone wins a name."""
        async with postgres.session() as session:
            rows = await session.execute(
                select(
                    PluginInstallationRow,
                    PluginRow.repository,
                    PluginRow.path,
                    _granted_by(user_id),
                )
                .join(PluginRow, PluginRow.id == PluginInstallationRow.plugin)
                .where(
                    or_(
                        PluginInstallationRow.user_id == user_id,
                        PluginInstallationRow.user_id.is_(None),
                    )
                )
                .order_by(PluginInstallationRow.created_at)
            )
        by_name: dict[str, Installation] = {}
        for row, repository, path, repositories in rows:
            try:
                installation = _installation(row, repository, path, repositories)
            except ValidationError:
                logger.warning(
                    "Skipping plugin installation with an unreadable manifest",
                    extra={"plugin_installation_id": str(row.id)},
                    exc_info=True,
                )
                continue
            held = by_name.get(installation.manifest.name)
            if held is None or (held.user_id is not None and installation.user_id is None):
                by_name[installation.manifest.name] = installation
        return list(by_name.values())

    @classmethod
    async def get(cls, installation_id: UUID, user_id: UUID) -> Installation:
        """The install, when ``user_id`` may use it."""
        for installation in await cls.visible_to(user_id):
            if installation.id == installation_id:
                return installation
        raise InstallationNotFound

    @staticmethod
    async def named(name: str, user_id: UUID | None) -> UUID | None:
        """The id of the install called ``name`` for ``user_id`` (``None``: for everyone)."""
        owner = (
            PluginInstallationRow.user_id.is_(None)
            if user_id is None
            else PluginInstallationRow.user_id == user_id
        )
        async with postgres.session() as session:
            return await session.scalar(
                select(PluginInstallationRow.id).where(
                    owner, PluginInstallationRow.manifest["name"].astext == name
                )
            )

    @staticmethod
    async def install(
        package: PluginPackage,
        *,
        user_id: UUID | None,
        by: UUID,
    ) -> UUID:
        """Install ``package`` for ``user_id``, or everyone when ``None``."""
        async with postgres.session() as session:
            plugin = await _plugin_id(session, package.source, by)
            bundle = await _bundle_id(session, package.html, by)
            row = PluginInstallationRow(
                plugin=plugin,
                user_id=user_id,
                ref=package.ref,
                commit_sha=package.commit_sha,
                manifest=package.manifest.model_dump(mode="json", by_alias=True),
                bundle=bundle,
                created_by=by,
                updated_by=by,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise PluginNameTaken(package.manifest.name) from exc
            return row.id

    @staticmethod
    async def upgrade(installation_id: UUID, package: PluginPackage, *, by: UUID) -> None:
        """Move the install to ``package``, a newer commit of the same source."""
        async with postgres.session() as session:
            bundle = await _bundle_id(session, package.html, by)
            await session.execute(
                update(PluginInstallationRow)
                .where(PluginInstallationRow.id == installation_id)
                .values(
                    ref=package.ref,
                    commit_sha=package.commit_sha,
                    manifest=package.manifest.model_dump(mode="json", by_alias=True),
                    bundle=bundle,
                    updated_at=func.clock_timestamp(),
                    updated_by=by,
                )
            )

    @staticmethod
    async def share(installation_id: UUID, user_id: UUID, repositories: Sequence[str]) -> None:
        """Let the install reach ``repositories`` for ``user_id``, who chose them."""
        if not repositories:
            return
        async with postgres.session() as session:
            await session.execute(
                insert(PluginRepositoryGrantRow)
                .values(
                    [
                        {
                            "id": uuid7(),
                            "installation": installation_id,
                            "user_id": user_id,
                            "repository": repository,
                            "created_by": user_id,
                            "updated_by": user_id,
                        }
                        for repository in repositories
                    ]
                )
                .on_conflict_do_nothing()
            )

    @staticmethod
    async def unshare(installation_id: UUID, user_id: UUID, repository: str) -> None:
        async with postgres.session() as session:
            await session.execute(
                delete(PluginRepositoryGrantRow).where(
                    PluginRepositoryGrantRow.installation == installation_id,
                    PluginRepositoryGrantRow.user_id == user_id,
                    PluginRepositoryGrantRow.repository == repository,
                )
            )

    @staticmethod
    async def remove(installation_id: UUID) -> None:
        async with postgres.session() as session:
            await session.execute(
                delete(PluginInstallationRow).where(PluginInstallationRow.id == installation_id)
            )

    @staticmethod
    async def bundle(installation_id: UUID) -> str:
        async with postgres.session() as session:
            html = await session.scalar(
                select(PluginBundleRow.html)
                .join(PluginInstallationRow, PluginInstallationRow.bundle == PluginBundleRow.id)
                .where(PluginInstallationRow.id == installation_id)
            )
        if html is None:
            raise InstallationNotFound
        return html


async def _plugin_id(session: AsyncSession, source: PluginSource, by: UUID) -> UUID:
    await session.execute(
        insert(PluginRow)
        .values(id=uuid7(), repository=source.repository, path=source.path, created_by=by)
        .on_conflict_do_nothing(index_elements=[PluginRow.repository, PluginRow.path])
    )
    return (
        await session.execute(
            select(PluginRow.id).where(
                PluginRow.repository == source.repository, PluginRow.path == source.path
            )
        )
    ).scalar_one()


async def _bundle_id(session: AsyncSession, html: str, by: UUID) -> UUID:
    digest = hashlib.sha256(html.encode()).hexdigest()
    await session.execute(
        insert(PluginBundleRow)
        .values(id=uuid7(), sha256=digest, html=html, created_by=by)
        .on_conflict_do_nothing(index_elements=[PluginBundleRow.sha256])
    )
    return (
        await session.execute(select(PluginBundleRow.id).where(PluginBundleRow.sha256 == digest))
    ).scalar_one()


@dataclass(frozen=True, slots=True)
class DocumentScope:
    """Who may read a plugin's documents: readers of ``repository``, or ``user_id`` alone."""

    repository: str | None = None
    user_id: UUID | None = None

    def topic_key(self, plugin: UUID) -> str:
        return (
            f"{plugin}/{self.repository}"
            if self.repository is not None
            else f"{plugin}/user/{self.user_id}"
        )


@dataclass(frozen=True, slots=True)
class PluginDocument:
    key: str
    document: JsonObject
    updated_at: datetime | None


class PluginDocuments:
    """One plugin's documents, the same for every installation of it."""

    def __init__(self, plugin: UUID) -> None:
        self.plugin = plugin

    def _where(self, collection: str, scope: DocumentScope) -> list[ColumnElement[bool]]:
        return [
            PluginDocumentRow.plugin == self.plugin,
            PluginDocumentRow.collection == collection,
            PluginDocumentRow.repository.is_(None)
            if scope.repository is None
            else PluginDocumentRow.repository == scope.repository,
            PluginDocumentRow.user_id.is_(None)
            if scope.user_id is None
            else PluginDocumentRow.user_id == scope.user_id,
        ]

    async def get(self, collection: str, scope: DocumentScope, key: str) -> PluginDocument | None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginDocumentRow).where(
                    *self._where(collection, scope), PluginDocumentRow.key == key
                )
            )
        return None if row is None else PluginDocument(row.key, row.document, row.updated_at)

    async def query(
        self, collection: str, scope: DocumentScope, where: JsonObject, *, limit: int
    ) -> list[PluginDocument]:
        """Documents whose fields include every field of ``where``, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(PluginDocumentRow)
                .where(*self._where(collection, scope), PluginDocumentRow.document.contains(where))
                .order_by(PluginDocumentRow.created_at)
                .limit(limit)
            )
            return [PluginDocument(row.key, row.document, row.updated_at) for row in rows]

    async def put(
        self, collection: str, scope: DocumentScope, key: str, document: JsonObject, *, by: UUID
    ) -> PluginDocument:
        async with postgres.session() as session:
            row = (
                await session.execute(
                    insert(PluginDocumentRow)
                    .values(
                        id=uuid7(),
                        plugin=self.plugin,
                        collection=collection,
                        key=key,
                        repository=scope.repository,
                        user_id=scope.user_id,
                        document=document,
                        created_by=by,
                        updated_by=by,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            PluginDocumentRow.plugin,
                            PluginDocumentRow.collection,
                            PluginDocumentRow.repository,
                            PluginDocumentRow.user_id,
                            PluginDocumentRow.key,
                        ],
                        set_={
                            "document": document,
                            "updated_at": func.clock_timestamp(),
                            "updated_by": by,
                        },
                    )
                    .returning(PluginDocumentRow)
                )
            ).scalar_one()
            await Topic.PLUGIN_DOCUMENTS.invalidate(session, key=scope.topic_key(self.plugin))
            return PluginDocument(row.key, row.document, row.updated_at)

    async def delete(self, collection: str, scope: DocumentScope, key: str) -> bool:
        async with postgres.session() as session:
            deleted = await session.scalar(
                delete(PluginDocumentRow)
                .where(*self._where(collection, scope), PluginDocumentRow.key == key)
                .returning(PluginDocumentRow.id)
            )
            if deleted is not None:
                await Topic.PLUGIN_DOCUMENTS.invalidate(session, key=scope.topic_key(self.plugin))
            return deleted is not None


class PluginThreads:
    """The agent threads one installation started."""

    def __init__(self, installation: UUID) -> None:
        self.installation = installation

    async def record(self, thread_id: str, *, by: UUID) -> None:
        async with postgres.session() as session:
            session.add(
                PluginThreadRow(
                    installation=self.installation,
                    thread_id=thread_id,
                    created_by=by,
                    updated_by=by,
                )
            )

    async def started_by(self, user_id: UUID, *, limit: int) -> list[str]:
        """Thread ids ``user_id`` started through this installation, newest first."""
        async with postgres.session() as session:
            ids = await session.scalars(
                select(PluginThreadRow.thread_id)
                .where(
                    PluginThreadRow.installation == self.installation,
                    PluginThreadRow.created_by == user_id,
                )
                .order_by(PluginThreadRow.created_at.desc())
                .limit(limit)
            )
            return list(ids)
