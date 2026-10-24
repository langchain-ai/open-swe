"""Installed plugins, their bundles, documents and threads in PostgreSQL."""

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid7

from pydantic import ValidationError
from sqlalchemy import (
    ColumnElement,
    ForeignKey,
    Numeric,
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
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, aggregate_order_by, array, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.repo_names import RepoFullName
from openswe.openai_responses.client_tools import ClientToolSpec
from openswe.plugins.manifest import PluginManifest
from openswe.plugins.source import PluginPackage, PluginSource
from openswe.run_config import PluginChatConfig
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

    @property
    def key(self) -> str:
        """``owner/repo``, or ``user/<id>`` for one person's own documents."""
        return self.repository if self.repository is not None else f"user/{self.user_id}"

    def topic_key(self, plugin: UUID, collection: str) -> str:
        """The ``plugin-documents`` key a write to ``collection`` in this scope invalidates."""
        return f"{plugin}/{collection}/{self.key}"


@dataclass(frozen=True, slots=True)
class PluginDocument:
    key: str
    document: JsonObject
    updated_at: datetime | None


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


# A dock that closed mid-call leaves its claim; after this, another dock may run the call.
CLAIM_TIMEOUT = timedelta(minutes=2)
_EPOCH = datetime.min.replace(tzinfo=UTC)


def tool_prefix(plugin_name: str) -> str:
    """What the names of a plugin's tools start with: no built-in tool name has ``__``."""
    return f"{plugin_name}__"


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
    async def run_config(thread_id: str) -> PluginChatConfig | None:
        """What a run of ``thread_id`` gets from the plugin it chats beside, if it is a plugin chat."""
        async with postgres.session() as session:
            found = (
                await session.execute(
                    select(PluginChatRow, PluginInstallationRow.manifest)
                    .join(
                        PluginInstallationRow,
                        PluginInstallationRow.id == PluginChatRow.installation,
                    )
                    .where(PluginChatRow.thread_id == thread_id)
                )
            ).one_or_none()
        if found is None:
            return None
        row, manifest = found
        plugin = PluginManifest.model_validate(manifest)
        chat = PluginChat.of(row)
        return PluginChatConfig(
            chat_id=str(chat.id),
            plugin=plugin.display_name,
            prefix=tool_prefix(plugin.name),
            tools=chat.tools,
            instructions=chat.instructions,
        )

    @property
    def calls(self) -> PluginChatCalls:
        return PluginChatCalls(self.id, self.thread_id)

    @staticmethod
    async def person_wrote(thread_id: str) -> list[dict[str, str]]:
        """The person wrote in the chat, so the plugin's results may resume it again.

        Returns the results no run took to the agent yet, for the person's
        message to carry, and counts them delivered.
        """
        async with postgres.session() as session:
            chat = await session.scalar(
                update(PluginChatRow)
                .where(PluginChatRow.thread_id == thread_id)
                .values(automatic_resumes=0)
                .returning(PluginChatRow.id)
            )
            if chat is None:
                return []
            held = await session.scalars(
                update(PluginChatCallRow)
                .where(
                    PluginChatCallRow.chat == chat,
                    PluginChatCallRow.answered_at.is_not(None),
                    PluginChatCallRow.delivered_at.is_(None),
                )
                .values(delivered_at=func.clock_timestamp(), updated_at=func.clock_timestamp())
                .returning(PluginChatCallRow)
            )
            return [
                ClientToolSpec.result_message(row.call_id, row.output or "")
                for row in sorted(held, key=lambda row: row.created_at or _EPOCH)
            ]


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
