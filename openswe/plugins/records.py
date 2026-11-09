"""Apps, their versions and installs, documents and chats in PostgreSQL."""

import asyncio
import hashlib
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final
from uuid import UUID, uuid7

from pydantic import ValidationError
from sqlalchemy import (
    ColumnElement,
    ForeignKey,
    LargeBinary,
    Numeric,
    Select,
    SQLColumnExpression,
    String,
    Text,
    case,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, aggregate_order_by, array, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, undefer

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.openai_responses.client_tools import ClientToolSpec
from openswe.plugins.manifest import PluginManifest, PluginPermission, read_bundle
from openswe.run_config import PluginChatConfig
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import JsonObject
from openswe.utils.object_store import ObjectStore

logger = logging.getLogger(__name__)


class PluginRow(Base):
    """An app: its owner publishes its versions, and its documents are its own."""

    __tablename__ = "plugin"

    owner: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str]
    listed: Mapped[bool] = mapped_column(default=False)
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


class PluginVersionRow(Base):
    __tablename__ = "plugin_version"

    plugin: Mapped[UUID] = mapped_column(ForeignKey("plugin.id", ondelete="CASCADE"))
    number: Mapped[int]
    manifest: Mapped[JsonObject] = mapped_column(JSONB)
    bundle: Mapped[UUID] = mapped_column(ForeignKey("plugin_bundle.id"))
    origin: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    preview_of: Mapped[str | None] = mapped_column(default=None)
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
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[UUID] = mapped_column(ForeignKey("plugin_version.id"))
    preview_of: Mapped[str | None] = mapped_column(default=None)
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

    @classmethod
    def shared_by(cls, user_id: UUID) -> ColumnElement[list[str]]:
        """The repositories ``user_id`` shared with the installation selected alongside, sorted."""
        return func.coalesce(
            select(func.array_agg(aggregate_order_by(cls.repository, cls.repository)))
            .where(cls.installation == PluginInstallationRow.id, cls.user_id == user_id)
            .scalar_subquery(),
            cast(array([], type_=Text), ARRAY(Text)),
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


class PluginDocumentAttachmentRow(Base):
    """A file an app keeps beside one of its documents, deleted with it."""

    __tablename__ = "plugin_document_attachment"

    document: Mapped[UUID] = mapped_column(ForeignKey("plugin_document.id", ondelete="CASCADE"))
    name: Mapped[str]
    content_type: Mapped[str]
    size: Mapped[int]
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True, default=None)
    """The file itself, when it is small enough to keep in PostgreSQL."""
    object_key: Mapped[str | None] = mapped_column(default=None)
    """Where object storage keeps it otherwise."""
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginChatRow(Base):
    __tablename__ = "plugin_chat"

    installation: Mapped[UUID] = mapped_column(
        ForeignKey("plugin_installation.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str]
    repository: Mapped[str | None]
    thread_id: Mapped[str]
    tool_specs: Mapped[list[JsonObject]] = mapped_column(JSONB, default_factory=list)
    instructions: Mapped[str] = mapped_column(default="")
    automatic_resumes: Mapped[int] = mapped_column(default=0)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PluginChatCallRow(Base):
    __tablename__ = "plugin_chat_call"

    chat: Mapped[UUID] = mapped_column(ForeignKey("plugin_chat.id", ondelete="CASCADE"))
    call_id: Mapped[str]
    tool: Mapped[str]
    arguments: Mapped[JsonObject] = mapped_column(JSONB)
    claimed_at: Mapped[datetime | None] = mapped_column(default=None)
    answered_at: Mapped[datetime | None] = mapped_column(default=None)
    output: Mapped[str | None] = mapped_column(default=None)
    delivered_at: Mapped[datetime | None] = mapped_column(default=None)
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
        super().__init__(f"You already have an app named {name!r}")


class InstallationNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("app not installed")


class AppNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("app not found")


class PreviewHeldByAnother(PermissionError):
    """A thread runs one preview, and another person's agent started it."""

    def __init__(self) -> None:
        super().__init__(
            "Another person's app preview runs beside this thread; only their builds replace it"
        )


@dataclass(frozen=True, slots=True)
class AppVersion:
    """One immutable build of an app."""

    id: UUID
    app: UUID
    number: int
    manifest: PluginManifest
    origin: JsonObject
    """Where it was built (repository, commit, thread), only to show."""
    preview_of: str | None
    """The thread whose preview it is; ``None`` for a release."""
    created_at: datetime | None

    @classmethod
    def of(cls, row: PluginVersionRow) -> AppVersion:
        return cls(
            id=row.id,
            app=row.plugin,
            number=row.number,
            manifest=PluginManifest.model_validate(row.manifest),
            origin=row.origin,
            preview_of=row.preview_of,
            created_at=row.created_at,
        )


@dataclass(frozen=True, slots=True)
class App:
    """An app someone may add to their web UI: their own, or a listed one."""

    id: UUID
    owner: UUID
    name: str
    listed: bool
    latest: AppVersion | None
    """Its newest release; ``None`` while it has only previews."""


@dataclass(frozen=True, slots=True)
class Installation:
    """One person's install of an app, pinned to a version.

    ``repositories`` are the ones that person shared with it.
    """

    id: UUID
    plugin: UUID
    user_id: UUID
    owner: UUID
    version: AppVersion
    repositories: tuple[str, ...]
    preview_of: str | None
    created_at: datetime | None

    @property
    def manifest(self) -> PluginManifest:
        return self.version.manifest


# Releases kept per app beyond those installed, to go back to.
KEPT_RELEASES = 5
# A bundle stored or reused this recently may be about to be used by a version in flight.
BUNDLE_GRACE = timedelta(hours=1)


class PluginApps:
    """Every app, and publishing their versions."""

    @classmethod
    async def publish(
        cls, owner: UUID, html: str, *, origin: JsonObject, preview_of: str | None = None
    ) -> AppVersion:
        """A new version of ``owner``'s app the bundle names, made the first time it is published.

        With ``preview_of`` it is that thread's preview, replacing the one before.
        """
        # A bundle can be 64 MB, too much to read on the event loop.
        manifest = await asyncio.to_thread(read_bundle, html)
        async with postgres.session() as session:
            await session.execute(
                insert(PluginRow)
                .values(id=uuid7(), owner=owner, name=manifest.name, created_by=owner)
                .on_conflict_do_nothing(index_elements=[PluginRow.owner, PluginRow.name])
            )
            app = (
                await session.execute(
                    select(PluginRow.id)
                    .where(PluginRow.owner == owner, PluginRow.name == manifest.name)
                    .with_for_update()
                )
            ).scalar_one()
            number = (
                await session.scalar(
                    select(func.coalesce(func.max(PluginVersionRow.number), 0)).where(
                        PluginVersionRow.plugin == app
                    )
                )
                or 0
            ) + 1
            row = PluginVersionRow(
                plugin=app,
                number=number,
                manifest=manifest.model_dump(mode="json", by_alias=True),
                bundle=await cls._bundle_id(session, html, owner),
                origin=origin,
                preview_of=preview_of,
                created_by=owner,
                updated_by=owner,
            )
            session.add(row)
            await session.flush()
            return AppVersion.of(row)

    @staticmethod
    async def _bundle_id(session: AsyncSession, html: str, by: UUID) -> UUID:
        """The stored bundle with ``html``, stored the first time it is published."""
        digest = hashlib.sha256(html.encode()).hexdigest()
        # Touching a bundle being reused keeps a concurrent cleanup from dropping it.
        return (
            await session.execute(
                insert(PluginBundleRow)
                .values(id=uuid7(), sha256=digest, html=html, created_by=by)
                .on_conflict_do_update(
                    index_elements=[PluginBundleRow.sha256],
                    set_={"updated_at": func.clock_timestamp(), "updated_by": by},
                )
                .returning(PluginBundleRow.id)
            )
        ).scalar_one()

    @staticmethod
    async def release(preview: AppVersion, *, by: UUID) -> AppVersion:
        """``preview``'s build as its app's next release."""
        async with postgres.session() as session:
            await session.execute(
                select(PluginRow.id).where(PluginRow.id == preview.app).with_for_update()
            )
            source = await session.get_one(PluginVersionRow, preview.id)
            number = await session.scalar(
                select(func.max(PluginVersionRow.number)).where(
                    PluginVersionRow.plugin == preview.app
                )
            )
            row = PluginVersionRow(
                plugin=preview.app,
                number=(number or 0) + 1,
                manifest=source.manifest,
                bundle=source.bundle,
                origin=source.origin,
                created_by=by,
                updated_by=by,
            )
            session.add(row)
            await session.flush()
            return AppVersion.of(row)

    @staticmethod
    async def available_to(user_id: UUID) -> list[App]:
        """The apps ``user_id`` may add: their own and listed ones, with each newest release."""
        latest = (
            select(PluginVersionRow)
            .where(PluginVersionRow.preview_of.is_(None))
            .distinct(PluginVersionRow.plugin)
            .order_by(PluginVersionRow.plugin, PluginVersionRow.number.desc())
            .subquery()
        )
        async with postgres.session() as session:
            rows = await session.execute(
                select(PluginRow, latest)
                .outerjoin(latest, latest.c.plugin == PluginRow.id)
                .where(or_(PluginRow.owner == user_id, PluginRow.listed.is_(True)))
                .order_by(PluginRow.name)
            )
            apps: list[App] = []
            for row in rows:
                app: PluginRow = row[0]
                version = (
                    AppVersion(
                        id=row.id,
                        app=app.id,
                        number=row.number,
                        manifest=PluginManifest.model_validate(row.manifest),
                        origin=row.origin,
                        preview_of=None,
                        created_at=row.created_at,
                    )
                    if row.number is not None
                    else None
                )
                apps.append(App(app.id, app.owner, app.name, app.listed, version))
            return apps

    @staticmethod
    async def newest_release(app_id: UUID) -> AppVersion | None:
        """The app's newest release, which anyone adding it now gets; ``None`` before the first."""
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginVersionRow)
                .where(PluginVersionRow.plugin == app_id, PluginVersionRow.preview_of.is_(None))
                .order_by(PluginVersionRow.number.desc())
                .limit(1)
            )
        return None if row is None else AppVersion.of(row)

    @staticmethod
    async def newest_preview(thread_id: str, owner: UUID) -> AppVersion | None:
        """The newest build ``owner``'s agent previewed in ``thread_id``, running or not."""
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginVersionRow)
                .join(PluginRow, PluginRow.id == PluginVersionRow.plugin)
                .where(PluginVersionRow.preview_of == thread_id, PluginRow.owner == owner)
                .order_by(PluginVersionRow.created_at.desc())
                .limit(1)
            )
        return None if row is None else AppVersion.of(row)

    @staticmethod
    async def version(version_id: UUID, user_id: UUID) -> AppVersion:
        """A release ``user_id`` may add: of their own app, or a listed one.

        A preview runs only beside its thread (``newest_preview``), so it is never one,
        even of a listed app.
        """
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginVersionRow)
                .join(PluginRow, PluginRow.id == PluginVersionRow.plugin)
                .where(
                    PluginVersionRow.id == version_id,
                    PluginVersionRow.preview_of.is_(None),
                    or_(PluginRow.owner == user_id, PluginRow.listed.is_(True)),
                )
            )
        if row is None:
            raise AppNotFound
        return AppVersion.of(row)

    @staticmethod
    async def set_listed(app_id: UUID, owner: UUID, listed: bool) -> None:
        """List the app for anyone to add, or keep it to its owner; only the owner may."""
        async with postgres.session() as session:
            changed = await session.scalar(
                update(PluginRow)
                .where(PluginRow.id == app_id, PluginRow.owner == owner)
                .values(listed=listed, updated_at=func.clock_timestamp(), updated_by=owner)
                .returning(PluginRow.id)
            )
        if changed is None:
            raise AppNotFound


class PluginInstallations:
    """Every person's installed apps."""

    @staticmethod
    async def _of(user_id: UUID, *where: ColumnElement[bool]) -> list[Installation]:
        async with postgres.session() as session:
            rows = await session.execute(
                select(
                    PluginInstallationRow,
                    PluginVersionRow,
                    PluginRow.owner,
                    PluginRepositoryGrantRow.shared_by(user_id),
                )
                .join(PluginVersionRow, PluginVersionRow.id == PluginInstallationRow.version)
                .join(PluginRow, PluginRow.id == PluginInstallationRow.plugin)
                .where(PluginInstallationRow.user_id == user_id, *where)
                .order_by(PluginInstallationRow.created_at)
            )
        found: list[Installation] = []
        for row, version, owner, repositories in rows:
            try:
                found.append(
                    Installation(
                        id=row.id,
                        plugin=row.plugin,
                        user_id=row.user_id,
                        owner=owner,
                        version=AppVersion.of(version),
                        repositories=tuple(repositories),
                        preview_of=row.preview_of,
                        created_at=row.created_at,
                    )
                )
            except ValidationError:
                logger.warning(
                    "Skipping app installation with an unreadable manifest",
                    extra={"plugin_installation_id": str(row.id)},
                    exc_info=True,
                )
        return found

    @classmethod
    async def visible_to(cls, user_id: UUID) -> list[Installation]:
        """The apps in the person's web UI; previews run in their threads instead."""
        return await cls._of(user_id, PluginInstallationRow.preview_of.is_(None))

    @classmethod
    async def get(cls, installation_id: UUID, user_id: UUID) -> Installation:
        """The person's install, a thread's preview included."""
        found = await cls._of(user_id, PluginInstallationRow.id == installation_id)
        if not found:
            raise InstallationNotFound
        return found[0]

    @classmethod
    async def install(cls, version: AppVersion, *, user_id: UUID) -> UUID:
        """Add ``version``'s app to ``user_id``'s web UI, pinned to it."""
        for installed in await cls.visible_to(user_id):
            if installed.manifest.name == version.manifest.name:
                raise PluginNameTaken(version.manifest.name)
        async with postgres.session() as session:
            row = PluginInstallationRow(
                plugin=version.app,
                user_id=user_id,
                version=version.id,
                created_by=user_id,
                updated_by=user_id,
            )
            session.add(row)
            await session.flush()
            return row.id

    @classmethod
    async def upgrade(cls, installation_id: UUID, version: AppVersion, *, by: UUID) -> None:
        """Move the install to ``version``, another of the same app."""
        async with postgres.session() as session:
            await session.execute(
                update(PluginInstallationRow)
                .where(
                    PluginInstallationRow.id == installation_id,
                    PluginInstallationRow.plugin == version.app,
                )
                .values(version=version.id, updated_at=func.clock_timestamp(), updated_by=by)
            )
            await cls._drop_unused(session, version.app)

    @classmethod
    async def preview(
        cls,
        thread_id: str,
        version: AppVersion,
        *,
        user_id: UUID,
        approved: Collection[PluginPermission] = (),
    ) -> bool:
        """Run ``version`` as ``thread_id``'s preview, in place of the one before.

        A build asking for permissions beyond the running preview's and
        ``approved`` waits for the person to approve them; false then. Raises
        ``PreviewHeldByAnother`` when someone else's preview runs there.
        """
        async with postgres.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"plugin-preview:{thread_id}"},
            )
            running = await cls.previewing(thread_id, user_id)
            granted = {
                *approved,
                *(
                    running.manifest.permissions
                    if running and running.plugin == version.app
                    else ()
                ),
            }
            await Topic.APP_PREVIEWS.invalidate(session, key=thread_id)
            if not set(version.manifest.permissions) <= granted:
                return False
            installed = await session.scalar(
                insert(PluginInstallationRow)
                .values(
                    id=uuid7(),
                    plugin=version.app,
                    user_id=user_id,
                    version=version.id,
                    preview_of=thread_id,
                    created_by=user_id,
                    updated_by=user_id,
                )
                .on_conflict_do_update(
                    index_elements=[PluginInstallationRow.preview_of],
                    index_where=PluginInstallationRow.preview_of.is_not(None),
                    set_={
                        "plugin": version.app,
                        "version": version.id,
                        "updated_at": func.clock_timestamp(),
                        "updated_by": user_id,
                    },
                    where=PluginInstallationRow.user_id == user_id,
                )
                .returning(PluginInstallationRow.id)
            )
            if installed is not None:
                await cls._drop_unused(session, version.app)
                if running is not None and running.plugin != version.app:
                    await session.execute(
                        delete(PluginRepositoryGrantRow).where(
                            PluginRepositoryGrantRow.installation == installed
                        )
                    )
                    await cls._drop_unused(session, running.plugin)
        # Raised after the commit, so the person still hears of the build they made.
        if installed is None:
            raise PreviewHeldByAnother
        return True

    @classmethod
    async def previewing(cls, thread_id: str, user_id: UUID) -> Installation | None:
        """The install running ``thread_id``'s preview for ``user_id``, if it has one."""
        found = await cls._of(user_id, PluginInstallationRow.preview_of == thread_id)
        return found[0] if found else None

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

    @classmethod
    async def remove(cls, installation_id: UUID, user_id: UUID) -> None:
        async with postgres.session() as session:
            app = await session.scalar(
                delete(PluginInstallationRow)
                .where(
                    PluginInstallationRow.id == installation_id,
                    PluginInstallationRow.user_id == user_id,
                )
                .returning(PluginInstallationRow.plugin)
            )
            if app is not None:
                await cls._drop_unused(session, app)

    @staticmethod
    async def bundle(installation_id: UUID) -> str:
        async with postgres.session() as session:
            html = await session.scalar(
                select(PluginBundleRow.html)
                .join(PluginVersionRow, PluginVersionRow.bundle == PluginBundleRow.id)
                .join(PluginInstallationRow, PluginInstallationRow.version == PluginVersionRow.id)
                .where(PluginInstallationRow.id == installation_id)
            )
        if html is None:
            raise InstallationNotFound
        return html

    @staticmethod
    async def _drop_unused(session: AsyncSession, app: UUID) -> None:
        """Forget ``app``'s versions no install runs, and bundles no version holds.

        Its newest releases stay, to go back to, and each thread's newest preview,
        which may wait for the person's approval; older previews go at once.
        """
        installed = select(PluginInstallationRow.version).where(PluginInstallationRow.plugin == app)
        releases = (
            select(PluginVersionRow.id)
            .where(PluginVersionRow.plugin == app, PluginVersionRow.preview_of.is_(None))
            .order_by(PluginVersionRow.number.desc())
            .limit(KEPT_RELEASES)
        )
        previews = (
            select(PluginVersionRow.id)
            .where(PluginVersionRow.plugin == app, PluginVersionRow.preview_of.is_not(None))
            .distinct(PluginVersionRow.preview_of)
            .order_by(PluginVersionRow.preview_of, PluginVersionRow.number.desc())
        )
        await session.execute(
            delete(PluginVersionRow).where(
                PluginVersionRow.plugin == app,
                PluginVersionRow.id.not_in(installed),
                PluginVersionRow.id.not_in(releases),
                PluginVersionRow.id.not_in(previews),
            )
        )
        await session.execute(
            delete(PluginBundleRow).where(
                PluginBundleRow.updated_at < func.clock_timestamp() - BUNDLE_GRACE,
                ~select(PluginVersionRow.id)
                .where(PluginVersionRow.bundle == PluginBundleRow.id)
                .exists(),
            )
        )


@dataclass(frozen=True, slots=True)
class DocumentScope:
    """Who may read a plugin's documents: readers of ``repository``, or ``user_id`` alone."""

    repository: str | None = None
    user_id: UUID | None = None

    @property
    def key(self) -> str:
        """``owner/repo``, or ``user/<id>`` for one person's own documents."""
        return self.repository if self.repository is not None else f"user/{self.user_id}"

    def topic_key(self, plugin: UUID, collection: str) -> str:
        """The ``plugin-documents`` key a write to ``collection`` in this scope invalidates.

        The plugin and collection fold into a fixed-length digest, so the key fits a topic
        with any repository's name; the scope stays as it is, which a subscription is
        authorized on.
        """
        digest = hashlib.sha256(f"{plugin}/{collection}".encode()).hexdigest()[:32]
        return f"{digest}/{self.key}"


@dataclass(frozen=True, slots=True)
class PluginDocument:
    key: str
    document: JsonObject
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file kept beside a document; ``content`` is read only when asked for."""

    name: str
    content_type: str
    size: int
    updated_at: datetime | None
    content: bytes | None = None


JsonScalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class Cursor:
    """Where a query's range starts or ends: a key, or a field's value and a key that breaks ties."""

    value: JsonScalar
    key: str | None = None
    inclusive: bool = True


@dataclass(frozen=True, slots=True)
class DocumentQuery:
    """
    Which documents to read and in what order: by key, or by one field and then key.

    Keys compare byte by byte. A field orders like Firebase's: missing or null,
    then false and true, numbers, strings byte by byte, and objects and arrays
    last. Cursors follow the direction: in descending order ``start`` is the
    highest.
    """

    where: JsonObject = field(default_factory=dict)
    order_by: tuple[str, ...] = ()
    descending: bool = False
    prefix: str | None = None
    start: Cursor | None = None
    end: Cursor | None = None
    limit: int = 200

    def _sort(self) -> tuple[SQLColumnExpression[object], ...]:
        key = PluginDocumentRow.key.collate("C")
        if not self.order_by:
            return (key,)
        value = PluginDocumentRow.document[self.order_by]
        kind = func.jsonb_typeof(value)
        return (
            case(
                (kind == "boolean", 1),
                (kind == "number", 2),
                (kind == "string", 3),
                (kind.in_(["object", "array"]), 4),
                else_=0,
            ),
            func.coalesce(case((kind == "number", cast(value.astext, Numeric))), 0),
            func.coalesce(case((kind.in_(["boolean", "string"]), value.astext)), "").collate("C"),
            key,
        )

    def _position(self, cursor: Cursor) -> tuple[SQLColumnExpression[object], ...]:
        if not self.order_by:
            return (literal(cursor.value, String),)
        match cursor.value:
            case None:
                rank, number, text = 0, 0, ""
            case bool():
                rank, number, text = 1, 0, "true" if cursor.value else "false"
            case int() | float():
                rank, number, text = 2, cursor.value, ""
            case str():
                rank, number, text = 3, 0, cursor.value
        position = [literal(rank), literal(number, Numeric), literal(text, String)]
        if cursor.key is not None:
            position.append(literal(cursor.key, String))
        return tuple(position)

    def _bound(self, cursor: Cursor, *, start: bool) -> ColumnElement[bool]:
        position = self._position(cursor)
        here = tuple_(*self._sort()[: len(position)])
        there = tuple_(*position)
        if start != self.descending:
            return here >= there if cursor.inclusive else here > there
        return here <= there if cursor.inclusive else here < there

    def clauses(self) -> list[ColumnElement[bool]]:
        clauses: list[ColumnElement[bool]] = []
        if self.where:
            clauses.append(PluginDocumentRow.document.contains(self.where))
        if self.prefix:
            clauses.append(PluginDocumentRow.key.startswith(self.prefix, autoescape=True))
        if self.start is not None:
            clauses.append(self._bound(self.start, start=True))
        if self.end is not None:
            clauses.append(self._bound(self.end, start=False))
        return clauses

    def order(self) -> list[SQLColumnExpression[object]]:
        return [column.desc() if self.descending else column.asc() for column in self._sort()]


_CREATE_ATTEMPTS = 3


class ConflictingDocumentWrites(RuntimeError):
    """Others kept creating and deleting a document while it was being created."""

    def __init__(self, key: str) -> None:
        super().__init__(f"{key} kept changing while it was being created; try again")


INLINE_ATTACHMENT_BYTES = 256 * 1024
"""The largest attachment kept in PostgreSQL; larger ones go to object storage."""

ATTACHMENT_SWEEP_JOB: Final = "plugin_attachment_sweep"
ATTACHMENT_SWEEP_SCHEDULE: Final = "23 * * * *"


class AttachmentStorageUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"files over {INLINE_ATTACHMENT_BYTES // 1024} KiB need object storage, "
            "which this Robin instance has not set up"
        )


@dataclass(frozen=True, slots=True)
class AttachmentObjects:
    """The attachments too large for PostgreSQL, as objects in object storage."""

    store: ObjectStore

    PREFIX: ClassVar[str] = "plugin-attachments/"
    # An object younger than this may belong to an upload whose row is not written yet.
    GRACE: ClassVar[timedelta] = timedelta(days=1)
    BATCH: ClassVar[int] = 500

    @classmethod
    def of(cls) -> AttachmentObjects:
        store = ObjectStore.configured()
        if store is None:
            raise AttachmentStorageUnavailable()
        return cls(store)

    async def put(self, content: bytes, content_type: str) -> str:
        """Stores ``content`` under a new key, which it returns."""
        object_key = f"{self.PREFIX}{uuid7()}"
        await self.store.put(object_key, content, content_type)
        return object_key

    async def read(self, object_key: str) -> bytes | None:
        return await self.store.get(object_key)

    async def sweep(self) -> int:
        """Deletes the objects no attachment names any longer; returns how many."""
        cutoff = datetime.now(UTC) - self.GRACE
        swept = 0
        batch: list[str] = []
        async for stored in self.store.list(self.PREFIX):
            if stored.last_modified < cutoff:
                batch.append(stored.key)
            if len(batch) >= self.BATCH:
                swept += await self._delete_unnamed(batch)
                batch = []
        if batch:
            swept += await self._delete_unnamed(batch)
        return swept

    async def _delete_unnamed(self, object_keys: list[str]) -> int:
        async with postgres.session() as session:
            named = set(
                await session.scalars(
                    select(PluginDocumentAttachmentRow.object_key).where(
                        PluginDocumentAttachmentRow.object_key.in_(object_keys)
                    )
                )
            )
        unnamed = [object_key for object_key in object_keys if object_key not in named]
        for object_key in unnamed:
            await self.store.delete(object_key)
        return len(unnamed)

    @staticmethod
    async def sweep_configured() -> None:
        """The sweep job: nothing to do where object storage is not set up."""
        store = ObjectStore.configured()
        if store is not None:
            logger.info(
                "Plugin attachment objects swept",
                extra={"swept": await AttachmentObjects(store).sweep()},
            )


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
        self, collection: str, scope: DocumentScope, query: DocumentQuery
    ) -> list[PluginDocument]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(PluginDocumentRow)
                .where(*self._where(collection, scope), *query.clauses())
                .order_by(*query.order())
                .limit(query.limit)
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
            await Topic.PLUGIN_DOCUMENTS.invalidate(
                session, key=scope.topic_key(self.plugin, collection)
            )
            return PluginDocument(row.key, row.document, row.updated_at)

    async def create(
        self, collection: str, scope: DocumentScope, key: str, document: JsonObject, *, by: UUID
    ) -> PluginDocument:
        """Stores ``document`` under ``key`` unless one is there; returns whichever is stored.

        Concurrent creates all get the same document back, so callers can tell who won.
        """
        # The stored document can be deleted between the conflict and the read; the key
        # is then free, so insert again, each time in a fresh snapshot.
        for _ in range(_CREATE_ATTEMPTS):
            async with postgres.session() as session:
                created = await session.scalar(
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
                    .on_conflict_do_nothing(
                        index_elements=[
                            PluginDocumentRow.plugin,
                            PluginDocumentRow.collection,
                            PluginDocumentRow.repository,
                            PluginDocumentRow.user_id,
                            PluginDocumentRow.key,
                        ],
                    )
                    .returning(PluginDocumentRow)
                )
                if created is not None:
                    await Topic.PLUGIN_DOCUMENTS.invalidate(
                        session, key=scope.topic_key(self.plugin, collection)
                    )
                    return PluginDocument(created.key, created.document, created.updated_at)
                stored = await session.scalar(
                    select(PluginDocumentRow).where(
                        *self._where(collection, scope), PluginDocumentRow.key == key
                    )
                )
                if stored is not None:
                    return PluginDocument(stored.key, stored.document, stored.updated_at)
        raise ConflictingDocumentWrites(key)

    async def delete(self, collection: str, scope: DocumentScope, key: str) -> bool:
        async with postgres.session() as session:
            deleted = await session.scalar(
                delete(PluginDocumentRow)
                .where(*self._where(collection, scope), PluginDocumentRow.key == key)
                .returning(PluginDocumentRow.id)
            )
            if deleted is not None:
                await Topic.PLUGIN_DOCUMENTS.invalidate(
                    session, key=scope.topic_key(self.plugin, collection)
                )
            return deleted is not None

    def _document_id(self, collection: str, scope: DocumentScope, key: str) -> Select[UUID]:
        return select(PluginDocumentRow.id).where(
            *self._where(collection, scope), PluginDocumentRow.key == key
        )

    async def attach(
        self,
        collection: str,
        scope: DocumentScope,
        key: str,
        name: str,
        content_type: str,
        content: bytes,
        *,
        by: UUID,
    ) -> Attachment | None:
        """Keeps ``content`` as ``name`` beside the document, replacing any file of that name.

        ``None`` when there is no document under ``key`` to attach it to. A file the row
        replaces, or one stored for a document deleted meanwhile, is left for the sweep.
        """
        objects = None if len(content) <= INLINE_ATTACHMENT_BYTES else AttachmentObjects.of()
        async with postgres.session() as session:
            if await session.scalar(self._document_id(collection, scope, key)) is None:
                return None
        # Uploaded outside any transaction, so no row lock waits on object storage.
        object_key = await objects.put(content, content_type) if objects else None
        inline = None if object_key else content
        async with postgres.session() as session:
            document = await session.scalar(
                self._document_id(collection, scope, key).with_for_update(key_share=True)
            )
            if document is None:
                return None
            row = (
                await session.execute(
                    insert(PluginDocumentAttachmentRow)
                    .values(
                        id=uuid7(),
                        document=document,
                        name=name,
                        content_type=content_type,
                        size=len(content),
                        content=inline,
                        object_key=object_key,
                        created_by=by,
                        updated_by=by,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            PluginDocumentAttachmentRow.document,
                            PluginDocumentAttachmentRow.name,
                        ],
                        set_={
                            "content_type": content_type,
                            "size": len(content),
                            "content": inline,
                            "object_key": object_key,
                            "updated_at": func.clock_timestamp(),
                            "updated_by": by,
                        },
                    )
                    .returning(
                        PluginDocumentAttachmentRow.name,
                        PluginDocumentAttachmentRow.content_type,
                        PluginDocumentAttachmentRow.updated_at,
                    )
                )
            ).one()
            await Topic.PLUGIN_DOCUMENTS.invalidate(
                session, key=scope.topic_key(self.plugin, collection)
            )
        return Attachment(row.name, row.content_type, len(content), row.updated_at)

    async def attachment(
        self, collection: str, scope: DocumentScope, key: str, name: str
    ) -> Attachment | None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginDocumentAttachmentRow)
                .where(
                    PluginDocumentAttachmentRow.document
                    == self._document_id(collection, scope, key).scalar_subquery(),
                    PluginDocumentAttachmentRow.name == name,
                )
                .options(undefer(PluginDocumentAttachmentRow.content))
            )
        if row is None:
            return None
        content = row.content
        if row.object_key is not None:
            content = await AttachmentObjects.of().read(row.object_key)
        if content is None:
            logger.warning(
                "A plugin attachment's object is missing", extra={"object_key": row.object_key}
            )
            return None
        return Attachment(row.name, row.content_type, row.size, row.updated_at, content=content)

    async def detach(self, collection: str, scope: DocumentScope, key: str, name: str) -> bool:
        async with postgres.session() as session:
            deleted = await session.scalar(
                delete(PluginDocumentAttachmentRow)
                .where(
                    PluginDocumentAttachmentRow.document
                    == self._document_id(collection, scope, key).scalar_subquery(),
                    PluginDocumentAttachmentRow.name == name,
                )
                .returning(PluginDocumentAttachmentRow.id)
            )
            if deleted is not None:
                await Topic.PLUGIN_DOCUMENTS.invalidate(
                    session, key=scope.topic_key(self.plugin, collection)
                )
            return deleted is not None


# A dock that closed mid-call leaves its claim; after this, another dock may run the call.
CLAIM_TIMEOUT = timedelta(minutes=2)
_EPOCH = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PluginChat:
    """One person's chat with the agent beside a plugin, and the tools the plugin offers it."""

    id: UUID
    installation: UUID
    user_id: UUID
    name: str
    repository: str | None
    thread_id: str
    tools: list[ClientToolSpec]
    instructions: str

    @classmethod
    def of(cls, row: PluginChatRow) -> PluginChat:
        return cls(
            id=row.id,
            installation=row.installation,
            user_id=row.user_id,
            name=row.name,
            repository=row.repository,
            thread_id=row.thread_id,
            tools=[ClientToolSpec.model_validate(spec) for spec in row.tool_specs],
            instructions=row.instructions,
        )

    @staticmethod
    async def run_config(thread_id: str, user_id: UUID) -> PluginChatConfig | None:
        """What a run of ``thread_id`` gets from the plugin it chats beside, if it is a plugin chat."""
        from openswe.users import User

        async with postgres.session() as session:
            found = (
                await session.execute(
                    select(
                        PluginChatRow,
                        PluginVersionRow.manifest,
                        PluginRepositoryGrantRow.shared_by(user_id),
                    )
                    .join(
                        PluginInstallationRow,
                        PluginInstallationRow.id == PluginChatRow.installation,
                    )
                    .join(PluginVersionRow, PluginVersionRow.id == PluginInstallationRow.version)
                    .where(PluginChatRow.thread_id == thread_id, PluginChatRow.user_id == user_id)
                )
            ).one_or_none()
        if found is None:
            return None
        row, manifest, repositories = found
        plugin = PluginManifest.model_validate(manifest)
        if PluginPermission.AGENT not in plugin.permissions:
            return None
        chat = PluginChat.of(row)
        if chat.repository is not None:
            if chat.repository not in repositories:
                return None
            user = await User.get(user_id)
            if user is None or not await user.can_read_repository(chat.repository):
                return None
        return PluginChatConfig(
            chat_id=str(chat.id),
            plugin=plugin.display_name,
            prefix=plugin.tool_prefix,
            tools=chat.tools,
            instructions=chat.instructions,
        )

    @property
    def calls(self) -> PluginChatCalls:
        return PluginChatCalls(self.id, self.thread_id)

    @staticmethod
    async def person_wrote(thread_id: str, user_id: UUID) -> list[dict[str, str]]:
        """The person wrote in the chat, so the plugin's results may resume it again.

        Returns the results no run took to the agent yet, for the person's
        message to carry. They stay held until the run is accepted.
        """
        async with postgres.session() as session:
            chat = await session.scalar(
                update(PluginChatRow)
                .where(PluginChatRow.thread_id == thread_id, PluginChatRow.user_id == user_id)
                .values(automatic_resumes=0)
                .returning(PluginChatRow.id)
            )
            if chat is None:
                return []
            held = await session.scalars(
                select(PluginChatCallRow).where(
                    PluginChatCallRow.chat == chat,
                    PluginChatCallRow.answered_at.is_not(None),
                    PluginChatCallRow.delivered_at.is_(None),
                )
            )
            return [
                ClientToolSpec.result_message(row.call_id, row.output or "")
                for row in sorted(held, key=lambda row: row.created_at or _EPOCH)
            ]

    @staticmethod
    async def deliver_input(thread_id: str, user_id: UUID, run_input: object) -> None:
        """A successfully dispatched run took these held results to the agent."""
        messages = run_input.get("messages") if isinstance(run_input, dict) else None
        if not isinstance(messages, list):
            return
        call_ids = [
            message["tool_call_id"]
            for message in messages
            if isinstance(message, dict)
            and message.get("type") == "tool"
            and isinstance(message.get("tool_call_id"), str)
        ]
        if not call_ids:
            return
        async with postgres.session() as session:
            await session.execute(
                update(PluginChatCallRow)
                .where(
                    PluginChatCallRow.chat.in_(
                        select(PluginChatRow.id).where(
                            PluginChatRow.thread_id == thread_id,
                            PluginChatRow.user_id == user_id,
                        )
                    ),
                    PluginChatCallRow.call_id.in_(call_ids),
                    PluginChatCallRow.answered_at.is_not(None),
                )
                .values(delivered_at=func.clock_timestamp(), updated_at=func.clock_timestamp())
            )


class PluginChats:
    """One person's chats with the agent beside one installation of a plugin."""

    def __init__(self, installation: UUID, user_id: UUID) -> None:
        self.installation = installation
        self.user_id = user_id

    def _where(self) -> list[ColumnElement[bool]]:
        return [
            PluginChatRow.installation == self.installation,
            PluginChatRow.user_id == self.user_id,
        ]

    async def get(self, name: str, repository: str | None) -> PluginChat | None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginChatRow).where(
                    *self._where(),
                    PluginChatRow.name == name,
                    PluginChatRow.repository.is_(None)
                    if repository is None
                    else PluginChatRow.repository == repository,
                )
            )
        return None if row is None else PluginChat.of(row)

    async def by_id(self, chat_id: UUID) -> PluginChat | None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginChatRow).where(*self._where(), PluginChatRow.id == chat_id)
            )
        return None if row is None else PluginChat.of(row)

    async def on_thread(self, thread_id: str) -> PluginChat | None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(PluginChatRow).where(*self._where(), PluginChatRow.thread_id == thread_id)
            )
        return None if row is None else PluginChat.of(row)

    async def record(self, name: str, repository: str | None, thread_id: str) -> PluginChat:
        """The chat under ``name``, recorded with ``thread_id`` unless it already was."""
        async with postgres.session() as session:
            await session.execute(
                insert(PluginChatRow)
                .values(
                    id=uuid7(),
                    installation=self.installation,
                    user_id=self.user_id,
                    name=name,
                    repository=repository,
                    thread_id=thread_id,
                    created_by=self.user_id,
                    updated_by=self.user_id,
                )
                .on_conflict_do_nothing()
            )
        chat = await self.get(name, repository)
        if chat is None:
            raise RuntimeError("plugin chat vanished as it was recorded")
        return chat

    async def offer(
        self, chat_id: UUID, tools: Sequence[ClientToolSpec], instructions: str
    ) -> bool:
        """Give the chat's next runs ``tools``; false when it is not this person's."""
        async with postgres.session() as session:
            updated = await session.scalar(
                update(PluginChatRow)
                .where(*self._where(), PluginChatRow.id == chat_id)
                .values(
                    tool_specs=[tool.model_dump(mode="json") for tool in tools],
                    instructions=instructions,
                    updated_at=func.clock_timestamp(),
                    updated_by=self.user_id,
                )
                .returning(PluginChatRow.id)
            )
        return updated is not None


@dataclass(frozen=True, slots=True)
class PluginChatCall:
    call_id: str
    tool: str
    arguments: JsonObject


class PluginChatCalls:
    """The calls a chat's agent made to the plugin's tools, which the plugin's frame answers."""

    def __init__(self, chat: UUID, thread_id: str) -> None:
        self.chat = chat
        self.thread_id = thread_id

    async def record(self, call_id: str, tool: str, arguments: JsonObject) -> None:
        async with postgres.session() as session:
            await session.execute(
                insert(PluginChatCallRow)
                .values(id=uuid7(), chat=self.chat, call_id=call_id, tool=tool, arguments=arguments)
                .on_conflict_do_nothing()
            )
            await Topic.PLUGIN_CHAT_CALLS.invalidate(session, key=self.thread_id)

    async def pending(self) -> list[PluginChatCall]:
        """Calls still waiting for the plugin, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(PluginChatCallRow)
                .where(PluginChatCallRow.chat == self.chat, PluginChatCallRow.answered_at.is_(None))
                .order_by(PluginChatCallRow.created_at)
            )
            return [PluginChatCall(row.call_id, row.tool, row.arguments) for row in rows]

    async def claim(self) -> list[PluginChatCall]:
        """Take the waiting calls no other dock is running, to run them; oldest first."""
        stale = func.clock_timestamp() - CLAIM_TIMEOUT
        async with postgres.session() as session:
            rows = await session.scalars(
                update(PluginChatCallRow)
                .where(
                    PluginChatCallRow.chat == self.chat,
                    PluginChatCallRow.answered_at.is_(None),
                    or_(
                        PluginChatCallRow.claimed_at.is_(None),
                        PluginChatCallRow.claimed_at < stale,
                    ),
                )
                .values(claimed_at=func.clock_timestamp(), updated_at=func.clock_timestamp())
                .returning(PluginChatCallRow)
            )
            claimed = sorted(rows, key=lambda row: row.created_at or _EPOCH)
            return [PluginChatCall(row.call_id, row.tool, row.arguments) for row in claimed]

    async def answer(self, outputs: Mapping[str, str], *, by: UUID) -> list[str]:
        """Keep what the frame returned for each call still waiting; those calls, so each is answered once."""
        answered: list[str] = []
        async with postgres.session() as session:
            for call_id, output in outputs.items():
                if await session.scalar(
                    update(PluginChatCallRow)
                    .where(
                        PluginChatCallRow.chat == self.chat,
                        PluginChatCallRow.call_id == call_id,
                        PluginChatCallRow.answered_at.is_(None),
                    )
                    .values(
                        output=output,
                        answered_at=func.clock_timestamp(),
                        updated_at=func.clock_timestamp(),
                        updated_by=by,
                    )
                    .returning(PluginChatCallRow.id)
                ):
                    answered.append(call_id)
            if answered:
                await Topic.PLUGIN_CHAT_CALLS.invalidate(session, key=self.thread_id)
        return answered

    async def deliver(self, call_ids: Sequence[str]) -> None:
        """A run took these calls' results to the agent."""
        async with postgres.session() as session:
            await session.execute(
                update(PluginChatCallRow)
                .where(PluginChatCallRow.chat == self.chat, PluginChatCallRow.call_id.in_(call_ids))
                .values(delivered_at=func.clock_timestamp(), updated_at=func.clock_timestamp())
            )

    async def reopen(self, call_ids: Sequence[str]) -> None:
        """Let ``call_ids`` be answered again, as when the run their answers started never began."""
        async with postgres.session() as session:
            await session.execute(
                update(PluginChatCallRow)
                .where(PluginChatCallRow.chat == self.chat, PluginChatCallRow.call_id.in_(call_ids))
                .values(
                    answered_at=None,
                    claimed_at=None,
                    output=None,
                    updated_at=func.clock_timestamp(),
                )
            )
            await Topic.PLUGIN_CHAT_CALLS.invalidate(session, key=self.thread_id)

    async def spend_resume(self, limit: int) -> bool:
        """Count one run the plugin's results start; false once ``limit`` ran since the person wrote."""
        async with postgres.session() as session:
            spent = await session.scalar(
                update(PluginChatRow)
                .where(PluginChatRow.id == self.chat, PluginChatRow.automatic_resumes < limit)
                .values(automatic_resumes=PluginChatRow.automatic_resumes + 1)
                .returning(PluginChatRow.id)
            )
            return spent is not None

    async def refund_resume(self) -> None:
        async with postgres.session() as session:
            await session.execute(
                update(PluginChatRow)
                .where(PluginChatRow.id == self.chat, PluginChatRow.automatic_resumes > 0)
                .values(automatic_resumes=PluginChatRow.automatic_resumes - 1)
            )
