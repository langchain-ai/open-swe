"""Dashboard API for publishing and adding apps, and for the calls their frames relay.

``/plugins`` publishes apps' versions and adds them to a person's web UI;
``/plugins/previews/{thread_id}`` is the one an agent builds in a thread. ``/plugins/{installation_id}/...`` is
everything a running plugin may ask for: its documents, its repositories on
GitHub, and agent threads. The dashboard relays those from the plugin's
sandboxed frame, which cannot reach the network itself.
"""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.principal import DashboardPrincipal
from openswe.github.http import (
    GitHubClient,
    GitHubError,
    GitHubSignInRequired,
    GraphQLError,
    RepoClient,
    UnsafeRepoPath,
)
from openswe.github.pull_request_status import OpenPullRequest, list_open_pull_requests
from openswe.github.repo_names import normalize_repo_full_name
from openswe.plugins.installed import (
    CollectionWrittenByApp,
    InstalledPlugin,
    PluginChatNotFound,
    PluginPermissionDenied,
    RepositoryNotGranted,
)
from openswe.plugins.manifest import (
    MAX_BUNDLE_BYTES,
    BundleError,
    CollectionName,
    PluginManifest,
    PluginPermission,
)
from openswe.plugins.records import (
    ATTACHMENT_TYPES,
    MAX_ATTACHMENT_BYTES,
    MAX_DOCUMENT_BYTES,
    AppNotFound,
    AppVersion,
    Attachment,
    AttachmentName,
    AttachmentStorageUnavailable,
    ConflictingDocumentWrites,
    Cursor,
    DocumentKey,
    DocumentQuery,
    Installation,
    InstallationNotFound,
    PluginApps,
    PluginChatCall,
    PluginDocument,
    PluginInstallations,
    PluginNameTaken,
    PreviewHeldByAnother,
)
from openswe.plugins.rooms import MAX_PRESENCE_BYTES, ROOMS, Room
from openswe.users.models import User
from openswe.utils.json_types import JsonObject
from openswe.utils.object_store import ObjectStoreError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

FieldPath = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}(\.[A-Za-z0-9_-]{1,64}){0,7}$")
]
RepoPath = Annotated[
    str, StringConstraints(min_length=1, max_length=1000), AfterValidator(RepoClient.inside)
]
"""A file in a repository, or a branch: no empty, ``.`` or ``..`` part and no backslash."""


@contextmanager
def _as_http() -> Iterator[None]:
    """Turn what a plugin call can fail with into the answer its frame sees."""
    try:
        yield
    except InstallationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PluginPermissionDenied, RepositoryNotGranted, CollectionWrittenByApp) as exc:
        raise HTTPException(403, str(exc)) from exc
    except PluginChatNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PluginNameTaken, PreviewHeldByAnother, ConflictingDocumentWrites) as exc:
        raise HTTPException(409, str(exc)) from exc
    except AttachmentStorageUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ObjectStoreError as exc:
        logger.warning("Object storage failed a plugin call", exc_info=True)
        raise HTTPException(502, "object storage could not be reached") from exc
    except BundleError as exc:
        raise HTTPException(422, str(exc)) from exc
    except UnsafeRepoPath as exc:
        raise HTTPException(422, str(exc)) from exc
    except AppNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except GitHubSignInRequired as exc:
        raise HTTPException(401, "github token unavailable, re-login required") from exc
    except GitHubError as exc:
        status = exc.response.status_code
        raise HTTPException(status if status in {403, 404, 409, 422} else 502, exc.message) from exc
    except GraphQLError as exc:
        raise HTTPException(422, exc.message) from exc
    except httpx2.HTTPError as exc:
        logger.warning("GitHub unreachable for a plugin call", exc_info=True)
        raise HTTPException(502, "GitHub could not be reached") from exc


async def _viewer(session: dict[str, object] = SESSION_DEP) -> User:
    user = await DashboardPrincipal.from_session(session).user()
    if user is None:
        raise HTTPException(409, "No Robin user record for this login yet")
    return user


ViewerDep = Annotated[User, Depends(_viewer)]


async def _installed(
    installation_id: UUID, viewer: ViewerDep, session: dict[str, object] = SESSION_DEP
) -> InstalledPlugin:
    with _as_http():
        installation = await PluginInstallations.get(installation_id, viewer.id)
    email = session.get("email")
    return InstalledPlugin(
        installation=installation, user=viewer, email=email if isinstance(email, str) else None
    )


PluginDep = Annotated[InstalledPlugin, Depends(_installed)]


# ---- Apps and installs -----------------------------------------------------------


class AppVersionModel(BaseModel):
    id: UUID
    number: int
    manifest: PluginManifest
    origin: JsonObject
    """Where it was built, as its publisher said; only to show."""
    preview: bool
    created_at: datetime | None

    @classmethod
    def of(cls, version: AppVersion) -> AppVersionModel:
        return cls(
            id=version.id,
            number=version.number,
            manifest=version.manifest,
            origin=version.origin,
            preview=version.preview_of is not None,
            created_at=version.created_at,
        )


class AppModel(BaseModel):
    id: UUID
    name: str
    mine: bool
    listed: bool
    latest: AppVersionModel | None
    """Its newest release; none while it has only previews."""


class PluginSummary(BaseModel):
    id: UUID
    plugin_id: UUID
    """The app's id, shared by every install of it; it keys its documents' topics."""
    name: str
    manifest: PluginManifest
    version: AppVersionModel
    mine: bool
    """Whether the viewer publishes the app."""
    repositories: list[str]
    """The repositories the viewer shared with it."""
    installed_at: datetime | None

    @classmethod
    def of(cls, installation: Installation) -> PluginSummary:
        return cls(
            id=installation.id,
            plugin_id=installation.plugin,
            name=installation.manifest.name,
            manifest=installation.manifest,
            version=AppVersionModel.of(installation.version),
            mine=installation.owner == installation.user_id,
            repositories=list(installation.repositories),
            installed_at=installation.created_at,
        )

    @classmethod
    async def get(cls, installation_id: UUID, user_id: UUID) -> PluginSummary:
        """The person's install as it is now."""
        with _as_http():
            return cls.of(await PluginInstallations.get(installation_id, user_id))


class PreviewModel(BaseModel):
    running: PluginSummary | None
    """The build running beside the thread."""
    waiting: AppVersionModel | None
    """A newer build that asks for permissions the viewer has not approved yet."""


class InstallBody(BaseModel):
    version: UUID
    permissions: list[PluginPermission]
    """The permissions the person approved; it fails if the version asks for more."""

    def require_approved(self, manifest: PluginManifest) -> None:
        """Pass when the person approved every permission ``manifest`` asks for."""
        if unapproved := sorted(set(manifest.permissions) - set(self.permissions)):
            raise HTTPException(
                409,
                {
                    "message": "The app asks for permissions you have not approved",
                    "permissions": unapproved,
                },
            )


class ListedBody(BaseModel):
    listed: bool


class RepositoriesBody(BaseModel):
    repositories: list[str] = Field(min_length=1, max_length=50)

    async def readable_by(self, user: User) -> list[str]:
        """The repositories as lowercased ``owner/repo``, once ``user`` can read each one."""
        try:
            names = sorted({normalize_repo_full_name(repo).lower() for repo in self.repositories})
        except ValueError as exc:
            raise HTTPException(422, "repositories must be owner/repo") from exc
        readable = await asyncio.gather(*(user.can_read_repository(name) for name in names))
        if missing := [name for name, ok in zip(names, readable, strict=True) if not ok]:
            raise HTTPException(403, f"You cannot reach {', '.join(missing)}")
        return names


@router.get("")
async def api_list_plugins(viewer: ViewerDep) -> list[PluginSummary]:
    return [PluginSummary.of(i) for i in await PluginInstallations.visible_to(viewer.id)]


@router.get("/apps")
async def api_list_apps(viewer: ViewerDep) -> list[AppModel]:
    """The apps the viewer may add: their own, and the ones others listed."""
    return [
        AppModel(
            id=app.id,
            name=app.name,
            mine=app.owner == viewer.id,
            listed=app.listed,
            latest=AppVersionModel.of(app.latest) if app.latest else None,
        )
        for app in await PluginApps.available_to(viewer.id)
    ]


@router.post("/apps", status_code=201)
@audit_endpoint
async def api_publish_app(
    request: Request,
    viewer: ViewerDep,
    repository: str | None = Query(None, max_length=200),
    ref: str | None = Query(None, max_length=200),
    commit: str | None = Query(None, max_length=64),
) -> AppVersionModel:
    """Release a version of one of the viewer's apps from its bundle, the request's HTML body.

    ``repository``, ``ref`` and ``commit`` say where it was built, only to show.
    """
    # Not a type a cross-site form can send, so a browser preflights it first.
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "text/html":
        raise HTTPException(415, "send the bundle as text/html")
    too_large = HTTPException(413, f"an app is at most {MAX_BUNDLE_BYTES} bytes")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BUNDLE_BYTES:
        raise too_large
    # A chunked upload names no length, so the limit holds as the body arrives.
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BUNDLE_BYTES:
            raise too_large
    try:
        html = body.decode()
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "the bundle must be UTF-8 HTML") from exc
    origin: JsonObject = {
        key: value
        for key, value in {"repository": repository, "ref": ref, "commit": commit}.items()
        if value
    }
    with _as_http():
        return AppVersionModel.of(await PluginApps.publish(viewer.id, html, origin=origin))


@router.patch("/apps/{app_id}", status_code=204)
@audit_endpoint
async def api_list_app(app_id: UUID, body: ListedBody, viewer: ViewerDep) -> Response:
    """List the app for anyone to add, or keep it to the viewer, who publishes it."""
    with _as_http():
        await PluginApps.set_listed(app_id, viewer.id, body.listed)
    return Response(status_code=204)


@router.post("", status_code=201)
@audit_endpoint
async def api_install_plugin(body: InstallBody, viewer: ViewerDep) -> PluginSummary:
    """Add a version of an app to the viewer's web UI."""
    with _as_http():
        version = await PluginApps.version(body.version, viewer.id)
        body.require_approved(version.manifest)
        installation_id = await PluginInstallations.install(version, user_id=viewer.id)
    return await PluginSummary.get(installation_id, viewer.id)


@router.get("/previews/{thread_id}")
async def api_app_preview(thread_id: str, viewer: ViewerDep) -> PreviewModel:
    """The app the viewer's agent builds in ``thread_id``, as it runs beside the thread."""
    running = await PluginInstallations.previewing(thread_id, viewer.id)
    newest = await PluginApps.newest_preview(thread_id, viewer.id)
    return PreviewModel(
        running=PluginSummary.of(running) if running else None,
        waiting=AppVersionModel.of(newest)
        if newest and (running is None or running.version.id != newest.id)
        else None,
    )


@router.post("/previews/{thread_id}")
@audit_endpoint
async def api_approve_app_preview(
    thread_id: str, body: InstallBody, viewer: ViewerDep
) -> PreviewModel:
    """Run the thread's newest build, with the permissions it asks for approved."""
    newest = await PluginApps.newest_preview(thread_id, viewer.id)
    if newest is None or newest.id != body.version:
        raise HTTPException(409, "The agent has built a newer preview since")
    body.require_approved(newest.manifest)
    with _as_http():
        await PluginInstallations.preview(
            thread_id, newest, user_id=viewer.id, approved=body.permissions
        )
    return await api_app_preview(thread_id, viewer)


@router.post("/previews/{thread_id}/release", status_code=201)
@audit_endpoint
async def api_release_app_preview(thread_id: str, viewer: ViewerDep) -> PluginSummary:
    """Keep the build running beside the thread: release it, in the viewer's sidebar."""
    running = await PluginInstallations.previewing(thread_id, viewer.id)
    if running is None or running.owner != viewer.id:
        raise HTTPException(404, "No preview runs beside this thread")
    with _as_http():
        version = await PluginApps.release(running.version, by=viewer.id)
        installed = next(
            (
                installation
                for installation in await PluginInstallations.visible_to(viewer.id)
                if installation.plugin == version.app
            ),
            None,
        )
        if installed is None:
            installation_id = await PluginInstallations.install(version, user_id=viewer.id)
        else:
            installation_id = installed.id
            await PluginInstallations.upgrade(installed.id, version, by=viewer.id)
    return await PluginSummary.get(installation_id, viewer.id)


@router.post("/{installation_id}/upgrade")
@audit_endpoint
async def api_upgrade_plugin(
    body: InstallBody, plugin: PluginDep, viewer: ViewerDep
) -> PluginSummary:
    """Move the install to another version of its app."""
    with _as_http():
        version = await PluginApps.version(body.version, viewer.id)
        if version.app != plugin.installation.plugin:
            raise HTTPException(409, "That is not a release of this app")
        body.require_approved(version.manifest)
        await PluginInstallations.upgrade(plugin.installation.id, version, by=viewer.id)
    return await PluginSummary.get(plugin.installation.id, viewer.id)


@router.post("/{installation_id}/repositories")
@audit_endpoint
async def api_share_plugin_repositories(
    body: RepositoriesBody, plugin: PluginDep, viewer: ViewerDep
) -> PluginSummary:
    """Share repositories with the app, for the viewer only: what they picked when it asked."""
    repositories = await body.readable_by(viewer)
    await PluginInstallations.share(plugin.installation.id, viewer.id, repositories)
    return await PluginSummary.get(plugin.installation.id, viewer.id)


@router.delete("/{installation_id}/repositories/{owner}/{repo}")
@audit_endpoint
async def api_unshare_plugin_repository(
    owner: str, repo: str, plugin: PluginDep, viewer: ViewerDep
) -> PluginSummary:
    await PluginInstallations.unshare(plugin.installation.id, viewer.id, f"{owner}/{repo}".lower())
    return await PluginSummary.get(plugin.installation.id, viewer.id)


@router.delete("/{installation_id}", status_code=204)
@audit_endpoint
async def api_uninstall_plugin(plugin: PluginDep, viewer: ViewerDep) -> Response:
    await PluginInstallations.remove(plugin.installation.id, viewer.id)
    return Response(status_code=204)


class BundleResponse(BaseModel):
    html: str


@router.get("/{installation_id}/bundle")
async def api_plugin_bundle(plugin: PluginDep) -> BundleResponse:
    with _as_http():
        return BundleResponse(html=await PluginInstallations.bundle(plugin.installation.id))


# ---- Storage -----------------------------------------------------------------------


class DocumentModel(BaseModel):
    key: str
    document: JsonObject
    updated_at: datetime | None

    @classmethod
    def of(cls, document: PluginDocument) -> DocumentModel:
        return cls(key=document.key, document=document.document, updated_at=document.updated_at)


class CursorModel(BaseModel):
    """A key, or with ``order_by`` the field's value and optionally the key that breaks ties."""

    value: StrictStr | StrictInt | StrictFloat | StrictBool | None
    key: DocumentKey | None = None
    inclusive: bool = True

    def cursor(self) -> Cursor:
        return Cursor(self.value, self.key, self.inclusive)


class DocumentQueryBody(BaseModel):
    repository: str | None = None
    where: JsonObject = Field(default_factory=dict)
    order_by: FieldPath | None = Field(
        None, description="A dotted field path; omitted orders by key."
    )
    direction: Literal["asc", "desc"] = "asc"
    prefix: Annotated[str, StringConstraints(max_length=200)] | None = None
    start: CursorModel | None = None
    end: CursorModel | None = None
    limit: int = Field(200, ge=1, le=500)

    @model_validator(mode="after")
    def _cursors_fit_the_order(self) -> DocumentQueryBody:
        if self.order_by is None:
            for cursor in (self.start, self.end):
                if cursor is not None and (not isinstance(cursor.value, str) or cursor.key):
                    raise ValueError("in key order a cursor's value is a key, with no separate key")
        return self

    def query(self) -> DocumentQuery:
        return DocumentQuery(
            where=self.where,
            order_by=tuple(self.order_by.split(".")) if self.order_by else (),
            descending=self.direction == "desc",
            prefix=self.prefix,
            start=self.start.cursor() if self.start else None,
            end=self.end.cursor() if self.end else None,
            limit=self.limit,
        )


class DocumentWrite(BaseModel):
    repository: str | None = None
    document: JsonObject


@router.post("/{installation_id}/documents/{collection}/query")
async def api_query_plugin_documents(
    collection: CollectionName, body: DocumentQueryBody, plugin: PluginDep
) -> list[DocumentModel]:
    with _as_http():
        scope = await plugin.document_scope(body.repository)
        found = await plugin.documents.query(collection, scope, body.query())
    return [DocumentModel.of(document) for document in found]


@router.get("/{installation_id}/documents/{collection}/{key:path}")
async def api_get_plugin_document(
    collection: CollectionName,
    key: DocumentKey,
    plugin: PluginDep,
    repository: str | None = None,
) -> DocumentModel:
    with _as_http():
        scope = await plugin.document_scope(repository)
        document = await plugin.documents.get(collection, scope, key)
    if document is None:
        raise HTTPException(404, "document not found")
    return DocumentModel.of(document)


@router.put("/{installation_id}/documents/{collection}/{key:path}")
async def api_put_plugin_document(
    collection: CollectionName,
    key: DocumentKey,
    body: DocumentWrite,
    plugin: PluginDep,
    if_absent: bool = False,
) -> DocumentModel:
    """Stores the document; with ``if_absent``, only when the key is free, returning what is stored."""
    if len(body.model_dump_json()) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, "document too large")
    with _as_http():
        scope = await plugin.writable_scope(collection, body.repository)
        write = plugin.documents.create if if_absent else plugin.documents.put
        document = await write(collection, scope, key, body.document, by=plugin.user.id)
    return DocumentModel.of(document)


@router.delete("/{installation_id}/documents/{collection}/{key:path}", status_code=204)
async def api_delete_plugin_document(
    collection: CollectionName,
    key: DocumentKey,
    plugin: PluginDep,
    repository: str | None = None,
) -> Response:
    with _as_http():
        scope = await plugin.writable_scope(collection, repository)
        if not await plugin.documents.delete(collection, scope, key):
            raise HTTPException(404, "document not found")
    return Response(status_code=204)


class AttachmentModel(BaseModel):
    name: str
    content_type: str
    size: int
    updated_at: datetime | None

    @classmethod
    def of(cls, attachment: Attachment) -> AttachmentModel:
        return cls(
            name=attachment.name,
            content_type=attachment.content_type,
            size=attachment.size,
            updated_at=attachment.updated_at,
        )


@router.put("/{installation_id}/attachments/{collection}/{name}")
async def api_put_plugin_attachment(
    collection: CollectionName,
    name: AttachmentName,
    request: Request,
    plugin: PluginDep,
    key: Annotated[DocumentKey, Query()],
    repository: str | None = None,
) -> AttachmentModel:
    """Keeps the request's body as ``name`` beside the document under ``key``.

    The body is an image or a video, sent as its own content type.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ATTACHMENT_TYPES:
        raise HTTPException(415, f"an attachment is one of {', '.join(sorted(ATTACHMENT_TYPES))}")
    too_large = HTTPException(413, f"an attachment is at most {MAX_ATTACHMENT_BYTES} bytes")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_ATTACHMENT_BYTES:
        raise too_large
    with _as_http():
        scope = await plugin.writable_scope(collection, repository)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_ATTACHMENT_BYTES:
            raise too_large
    if not body:
        raise HTTPException(422, "an attachment is not empty")
    with _as_http():
        attached = await plugin.documents.attach(
            collection, scope, key, name, content_type, bytes(body), by=plugin.user.id
        )
    if attached is None:
        raise HTTPException(404, "document not found")
    return AttachmentModel.of(attached)


@router.get(
    "/{installation_id}/attachments/{collection}/{name}",
    response_class=Response,
    responses={200: {"content": {media: {} for media in sorted(ATTACHMENT_TYPES)}}},
)
async def api_get_plugin_attachment(
    collection: CollectionName,
    name: AttachmentName,
    plugin: PluginDep,
    key: Annotated[DocumentKey, Query()],
    repository: str | None = None,
) -> Response:
    """An attachment's bytes, which the dashboard hands the plugin's frame as a Blob."""
    with _as_http():
        scope = await plugin.document_scope(repository)
        found = await plugin.documents.attachment(collection, scope, key, name)
    if found is None or found.content is None:
        raise HTTPException(404, "attachment not found")
    return Response(
        found.content,
        media_type=found.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": "attachment",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{installation_id}/attachments/{collection}/{name}", status_code=204)
async def api_delete_plugin_attachment(
    collection: CollectionName,
    name: AttachmentName,
    plugin: PluginDep,
    key: Annotated[DocumentKey, Query()],
    repository: str | None = None,
) -> Response:
    with _as_http():
        scope = await plugin.writable_scope(collection, repository)
        if not await plugin.documents.detach(collection, scope, key, name):
            raise HTTPException(404, "attachment not found")
    return Response(status_code=204)


# ---- GitHub ------------------------------------------------------------------------


class RepositoryInfo(BaseModel):
    full_name: str
    default_branch: str
    private: bool


class RefResponse(BaseModel):
    sha: str | None
    """``None`` when no branch, tag or commit has that name."""


class TreeEntryModel(BaseModel):
    path: str
    type: Literal["blob", "tree", "commit"]
    size: int | None


class TreeResponse(BaseModel):
    sha: str
    truncated: bool
    entries: list[TreeEntryModel]


class FileResponse(BaseModel):
    path: str
    text: str


class _GitAuthor(BaseModel):
    name: str = ""
    date: str | None = None


class _GitCommit(BaseModel):
    message: str = ""
    author: _GitAuthor | None = None


class _GitHubAccount(BaseModel):
    login: str | None = None


class _GitHubCommit(BaseModel):
    """The fields of GitHub's commit a plugin sees in a file's history."""

    sha: str
    commit: _GitCommit = Field(default_factory=_GitCommit)
    author: _GitHubAccount | None = None


_COMMITS = TypeAdapter(list[_GitHubCommit])


class HistoryEntry(BaseModel):
    sha: str
    message: str
    author_name: str
    author_login: str | None
    authored_at: str | None

    @classmethod
    def of(cls, commit: _GitHubCommit) -> HistoryEntry:
        author = commit.commit.author
        return cls(
            sha=commit.sha,
            message=commit.commit.message,
            author_name=author.name if author else "",
            author_login=commit.author.login if commit.author else None,
            authored_at=author.date if author else None,
        )


class BranchBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class CommitBody(BaseModel):
    branch: str = Field(min_length=1, max_length=200)
    expected_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    message: str = Field(min_length=1, max_length=10_000)
    files: dict[RepoPath, str] = Field(default_factory=dict, max_length=100)
    deletions: list[RepoPath] = Field(default_factory=list, max_length=100)


class _GitHubRepository(BaseModel):
    full_name: str = ""


class _GitHubBranch(BaseModel):
    ref: str = ""
    sha: str = ""
    repo: _GitHubRepository | None = None


class _GitHubTeam(BaseModel):
    slug: str


class _GitHubPull(BaseModel):
    """The fields of GitHub's pull request a plugin sees."""

    number: int
    title: str | None = None
    html_url: str | None = None
    state: str | None = None
    draft: bool | None = None
    merged: bool | None = None
    head: _GitHubBranch | None = None
    base: _GitHubBranch | None = None
    user: _GitHubAccount | None = None
    created_at: str | None = None
    updated_at: str | None = None
    requested_reviewers: list[_GitHubAccount] = Field(default_factory=list)
    requested_teams: list[_GitHubTeam] = Field(default_factory=list)


class PullRequestModel(BaseModel):
    number: int
    title: str
    url: str
    state: str
    merged: bool
    draft: bool
    head: str
    base: str
    head_sha: str
    base_sha: str
    author: str | None
    created_at: str | None
    updated_at: str | None
    requested_reviewers: list[str]
    """Logins GitHub still asks to review; a review answers the request."""
    requested_teams: list[str]
    """``org/slug`` of the teams GitHub still asks to review."""

    @classmethod
    def of(cls, payload: Mapping[str, object]) -> PullRequestModel:
        pull = _GitHubPull.model_validate(payload)
        base_repo = pull.base.repo.full_name if pull.base and pull.base.repo else ""
        org = base_repo.split("/", 1)[0]
        return cls(
            number=pull.number,
            title=pull.title or "",
            url=pull.html_url or "",
            state=pull.state or "open",
            merged=bool(pull.merged),
            draft=bool(pull.draft),
            head=pull.head.ref if pull.head else "",
            base=pull.base.ref if pull.base else "",
            head_sha=pull.head.sha if pull.head else "",
            base_sha=pull.base.sha if pull.base else "",
            author=pull.user.login if pull.user else None,
            created_at=pull.created_at,
            updated_at=pull.updated_at,
            requested_reviewers=[
                account.login for account in pull.requested_reviewers if account.login
            ],
            requested_teams=[f"{org}/{team.slug}" for team in pull.requested_teams],
        )


class PullRequestSummary(BaseModel):
    """A pull request as GitHub's search lists it, before reading it in full."""

    repository: str
    number: int
    title: str
    url: str
    draft: bool
    author: str | None
    created_at: str | None
    updated_at: str | None

    @classmethod
    def of(cls, pull: OpenPullRequest) -> PullRequestSummary:
        return cls(
            repository=pull.repo.lower(),
            number=pull.number,
            title=pull.title,
            url=f"https://github.com/{pull.repo}/pull/{pull.number}",
            draft=bool(pull.draft),
            author=pull.author,
            created_at=pull.created_at,
            updated_at=pull.updated_at,
        )


class PullRequestSearch(BaseModel):
    pulls: list[PullRequestSummary]
    incomplete: bool
    """Whether GitHub left some out: past its first page, or a search that timed out."""


class PullRequestBody(BaseModel):
    head: str = Field(min_length=1, max_length=200)
    base: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=60_000)
    draft: bool = False


@router.get("/{installation_id}/github/{owner}/{repo}")
async def api_plugin_repository(owner: str, repo: str, plugin: PluginDep) -> RepositoryInfo:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            info = await client.info()
    return RepositoryInfo(
        full_name=str(info.get("full_name") or client.full_name),
        default_branch=str(info.get("default_branch") or "main"),
        private=bool(info.get("private")),
    )


@router.get("/{installation_id}/github/{owner}/{repo}/ref")
async def api_plugin_ref(
    owner: str, repo: str, plugin: PluginDep, name: str = Query(min_length=1, max_length=200)
) -> RefResponse:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            try:
                return RefResponse(sha=await client.commit_sha(name))
            except GitHubError as exc:
                # GitHub answers a name that names nothing with 404 or 422.
                if exc.response.status_code not in {404, 422}:
                    raise
                return RefResponse(sha=None)


@router.get("/{installation_id}/github/{owner}/{repo}/tree")
async def api_plugin_tree(
    owner: str, repo: str, plugin: PluginDep, sha: str = Query(pattern=r"^[0-9a-f]{40}$")
) -> TreeResponse:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            tree = await client.tree(sha)
    return TreeResponse(
        sha=tree.sha,
        truncated=tree.truncated,
        entries=[TreeEntryModel(path=e.path, type=e.type, size=e.size) for e in tree.tree],
    )


@router.get("/{installation_id}/github/{owner}/{repo}/file")
async def api_plugin_file(
    owner: str,
    repo: str,
    plugin: PluginDep,
    path: Annotated[RepoPath, Query()],
    ref: str = Query(min_length=1, max_length=200),
) -> FileResponse:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            found = await client.read_files([path], ref)
    if path not in found:
        raise HTTPException(404, "file not found")
    return FileResponse(path=path, text=found[path])


IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


@router.get(
    "/{installation_id}/github/{owner}/{repo}/image",
    response_class=Response,
    responses={200: {"content": {media: {} for media in sorted(set(IMAGE_TYPES.values()))}}},
)
async def api_plugin_image(
    owner: str,
    repo: str,
    plugin: PluginDep,
    path: Annotated[RepoPath, Query()],
    ref: str = Query(min_length=1, max_length=200),
) -> Response:
    """An image in the repository, as bytes the plugin's frame can show; its own policy loads none."""
    media = IMAGE_TYPES.get(PurePosixPath(path).suffix.lower())
    if media is None:
        raise HTTPException(415, "not an image")
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            data = await client.raw_file(path, ref)
    if data is None:
        raise HTTPException(404, "image not found")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large")
    # An SVG is shown as an image, never as a document that could run its scripts.
    return Response(
        data, media_type=media, headers={"Content-Security-Policy": "default-src 'none'"}
    )


@router.get("/{installation_id}/github/{owner}/{repo}/history")
async def api_plugin_history(
    owner: str,
    repo: str,
    plugin: PluginDep,
    path: Annotated[RepoPath, Query()],
    ref: str = Query(min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
) -> list[HistoryEntry]:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            commits = _COMMITS.validate_python(
                await client.commits(path=path, ref=ref, limit=limit)
            )
    return [HistoryEntry.of(commit) for commit in commits]


@router.post("/{installation_id}/github/{owner}/{repo}/branches", status_code=204)
async def api_plugin_create_branch(
    owner: str, repo: str, body: BranchBody, plugin: PluginDep
) -> Response:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}", write=True) as client:
            await client.create_branch(body.name, body.sha)
    return Response(status_code=204)


@router.delete("/{installation_id}/github/{owner}/{repo}/branches/{name:path}", status_code=204)
@audit_endpoint
async def api_plugin_delete_branch(
    owner: str, repo: str, name: RepoPath, plugin: PluginDep
) -> Response:
    """Delete a branch, such as a draft's; never the repository's default branch."""
    with _as_http():
        async with plugin.github(f"{owner}/{repo}", write=True) as client:
            if name == (await client.info()).get("default_branch"):
                raise HTTPException(403, "the default branch can't be deleted")
            await client.delete_branch(name)
    return Response(status_code=204)


@router.post("/{installation_id}/github/{owner}/{repo}/commits")
@audit_endpoint
async def api_plugin_commit(
    owner: str, repo: str, body: CommitBody, plugin: PluginDep
) -> RefResponse:
    if not body.files and not body.deletions:
        raise HTTPException(422, "nothing to commit")
    with _as_http():
        async with plugin.github(f"{owner}/{repo}", write=True) as client:
            sha = await client.commit_files(
                branch=body.branch,
                expected_head=body.expected_head,
                message=body.message,
                additions=body.files,
                deletions=body.deletions,
            )
    return RefResponse(sha=sha)


@router.get("/{installation_id}/github/pulls")
async def api_plugin_search_pulls(
    plugin: PluginDep, role: Literal["review-requested", "author"]
) -> PullRequestSearch:
    """Open pull requests in the repositories the person shared and can read, by their role."""
    with _as_http():
        plugin.require(PluginPermission.GITHUB_READ, PluginPermission.GITHUB_WRITE)
        readable = await plugin.readable_repositories()
        if not readable:
            return PullRequestSearch(pulls=[], incomplete=False)
        async with GitHubClient.as_user(plugin.user.github_login) as github:
            # GitHub's review-requested qualifier includes requests to the person's teams.
            found = await list_open_pull_requests(
                github,
                plugin.user.github_login,
                ",".join(readable),
                scope="mine" if role == "author" else "review-requested",
                lightweight=True,
            )
    return PullRequestSearch(
        pulls=[PullRequestSummary.of(pull) for pull in found.pull_requests],
        incomplete=found.incomplete or found.next_page is not None,
    )


@router.get("/{installation_id}/github/{owner}/{repo}/pulls/{number}")
async def api_plugin_pull(
    owner: str, repo: str, number: Annotated[int, Path(ge=1, le=2**31 - 1)], plugin: PluginDep
) -> PullRequestModel:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            return PullRequestModel.of(await client.pull_request(number).pull())


@router.get("/{installation_id}/github/{owner}/{repo}/pulls")
async def api_plugin_pull_for_branch(
    owner: str, repo: str, plugin: PluginDep, head: str = Query(min_length=1, max_length=200)
) -> PullRequestModel | None:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}") as client:
            pull = await client.open_pull_for_branch(head)
    return None if pull is None else PullRequestModel.of(pull)


@router.post("/{installation_id}/github/{owner}/{repo}/pulls", status_code=201)
@audit_endpoint
async def api_plugin_open_pull(
    owner: str, repo: str, body: PullRequestBody, plugin: PluginDep
) -> PullRequestModel:
    with _as_http():
        async with plugin.github(f"{owner}/{repo}", write=True) as client:
            pull = await client.open_pull_request(
                head=body.head, base=body.base, title=body.title, body=body.body, draft=body.draft
            )
    return PullRequestModel.of(pull)


# ---- Agent chats -------------------------------------------------------------------

ChatName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:@/-]{1,200}$")]
ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
MAX_CHAT_TOOLS = 32


class ChatBody(BaseModel):
    name: ChatName
    repository: str | None = None


class ChatOpened(BaseModel):
    chat_id: UUID
    thread_id: str


class ChatToolBody(BaseModel):
    name: ToolName
    description: str = Field(max_length=2_000)
    parameters: JsonObject = Field(description="A JSON Schema for the tool's arguments object.")


class ChatToolsBody(BaseModel):
    tools: list[ChatToolBody] = Field(max_length=MAX_CHAT_TOOLS)
    instructions: str = Field("", max_length=4_000)


class ChatCallModel(BaseModel):
    call_id: str
    tool: str = Field(description="The tool's own name, as the plugin offered it.")
    arguments: JsonObject

    @classmethod
    def of(cls, call: PluginChatCall, manifest: PluginManifest) -> ChatCallModel:
        return cls(
            call_id=call.call_id,
            tool=call.tool.removeprefix(manifest.tool_prefix),
            arguments=call.arguments,
        )


class ChatOutputBody(BaseModel):
    call_id: str = Field(min_length=1, max_length=200)
    output: str = Field(max_length=200_000)


class ChatOutputsBody(BaseModel):
    outputs: list[ChatOutputBody] = Field(min_length=1, max_length=MAX_CHAT_TOOLS)


class ChatResumed(BaseModel):
    run_id: str | None
    paused: bool = Field(
        description="The agent stopped to wait for the person: the plugin's results resumed it too often."
    )


@router.post("/{installation_id}/chats")
async def api_open_plugin_chat(body: ChatBody, plugin: PluginDep) -> ChatOpened:
    """The person's chat under ``name`` beside the plugin, made the first time it is opened."""
    with _as_http():
        chat = await plugin.chat(body.name, body.repository)
    return ChatOpened(chat_id=chat.id, thread_id=chat.thread_id)


@router.put("/{installation_id}/chats/{chat_id}/tools", status_code=204)
async def api_offer_plugin_chat_tools(
    chat_id: UUID, body: ChatToolsBody, plugin: PluginDep
) -> None:
    """The tools the plugin's frame runs for this chat's next runs, replacing those before."""
    with _as_http():
        chat = await plugin.own_chat(chat_id)
        tools = [plugin.tool(t.name, t.description, t.parameters) for t in body.tools]
    if any(len(tool.name) > 64 for tool in tools):
        raise HTTPException(422, "a tool's name and the plugin's together exceed 64 characters")
    if len({tool.name for tool in tools}) < len(tools):
        raise HTTPException(422, "tool names must be unique")
    await plugin.chats.offer(chat.id, tools, body.instructions)


@router.get("/{installation_id}/chats/{chat_id}/calls")
async def api_plugin_chat_calls(chat_id: UUID, plugin: PluginDep) -> list[ChatCallModel]:
    """Calls the chat's agent made to the plugin's tools that wait for the plugin's frame."""
    with _as_http():
        chat = await plugin.own_chat(chat_id)
    manifest = plugin.installation.manifest
    return [ChatCallModel.of(call, manifest) for call in await chat.calls.pending()]


@router.post("/{installation_id}/chats/{chat_id}/calls/claim")
async def api_claim_plugin_chat_calls(chat_id: UUID, plugin: PluginDep) -> list[ChatCallModel]:
    """Take the waiting calls to run them in this dock's frame, so no other open dock runs them too."""
    with _as_http():
        chat = await plugin.own_chat(chat_id)
    manifest = plugin.installation.manifest
    return [ChatCallModel.of(call, manifest) for call in await chat.calls.claim()]


@router.post("/{installation_id}/chats/{chat_id}/outputs")
@audit_endpoint
async def api_answer_plugin_chat_calls(
    chat_id: UUID, body: ChatOutputsBody, plugin: PluginDep
) -> ChatResumed:
    """Resume the chat with what the plugin's frame returned for its calls."""
    with _as_http():
        chat = await plugin.own_chat(chat_id)
        answer = await plugin.answer(chat, {o.call_id: o.output for o in body.outputs})
    return ChatResumed(run_id=answer.run_id, paused=answer.paused)


# ---- People ------------------------------------------------------------------------


class Person(BaseModel):
    login: str
    name: str
    avatar_url: str | None


@router.get("/{installation_id}/people")
async def api_plugin_people(
    _plugin: PluginDep,
    login: Annotated[list[str], Query(min_length=1, max_length=100)],
) -> list[Person]:
    """Names and pictures for GitHub logins: Open SWE's record of each, else the login alone."""
    found = await User.for_github_logins(login)
    people: list[Person] = []
    for name in dict.fromkeys(login):
        user = found.get(name.lower())
        people.append(
            Person(
                login=name,
                name=(user.display_name if user else "") or name,
                avatar_url=(user.avatar_url if user else "")
                or f"https://avatars.githubusercontent.com/{quote(name, safe='')}",
            )
        )
    return people


# ---- Live rooms --------------------------------------------------------------------

RoomName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:@/-]{1,200}$")]
MAX_UPDATE_BYTES = 1_000_000
MAX_SNAPSHOT_BYTES = 8_000_000
ROOM_HEARTBEAT_SECONDS = 15.0
_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


class _RoomBody(BaseModel):
    repository: str | None = None

    @staticmethod
    def decode(value: str, limit: int) -> bytes:
        """``value``'s bytes, once they are base64 of at most ``limit`` bytes."""
        try:
            data = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise HTTPException(422, "payload must be base64") from exc
        if len(data) > limit:
            raise HTTPException(413, "payload too large")
        return data


class RoomUpdateBody(_RoomBody):
    update: str = Field(description="Base64 of the update's bytes")

    def payload(self) -> bytes:
        return self.decode(self.update, MAX_UPDATE_BYTES)


class RoomCompactBody(_RoomBody):
    snapshot: str = Field(description="Base64 of a state covering every update through `through`")
    through: int = Field(ge=0)

    def payload(self) -> bytes:
        return self.decode(self.snapshot, MAX_SNAPSHOT_BYTES)


class RoomPresenceBody(_RoomBody):
    client: str = Field(min_length=1, max_length=64)
    presence: str = Field(max_length=MAX_PRESENCE_BYTES, description="Base64 presence state")


class RoomSeq(BaseModel):
    seq: int


MAX_ROOMS_PER_STREAM = 20


class RoomJoin(BaseModel):
    room: RoomName
    repository: str | None = None
    after: int = Field(0, ge=0)


class _RoomStream:
    """Every room one frame joined, as server-sent events, for as long as the person may be in them.

    One stream carries them all: browsers allow a host only a handful of HTTP/1.1
    connections, and a page joins several rooms.
    """

    def __init__(self, plugin: InstalledPlugin, joins: Sequence[RoomJoin]) -> None:
        self.plugin = plugin
        self.joins = joins
        self.rooms: dict[str, Room] = {}
        self.seqs: dict[str, int] = {}

    async def join(self) -> None:
        """Enter each room, as the person may now; fails as any plugin call does."""
        for join in self.joins:
            room = await self.plugin.room(join.room, join.repository)
            self.rooms[room.key] = room
            self.seqs[room.key] = join.after

    async def allowed(self) -> bool:
        """Whether the person may still be in every room: the install, its ``realtime``
        permission, the repositories shared with it, and their own access to those."""
        try:
            installation = await PluginInstallations.get(
                self.plugin.installation.id, self.plugin.user.id
            )
            plugin = replace(self.plugin, installation=installation)
            keys = {(await plugin.room(join.room, join.repository)).key for join in self.joins}
        except (
            InstallationNotFound,
            PluginPermissionDenied,
            RepositoryNotGranted,
            HTTPException,
        ) as exc:
            logger.info(
                "Closed a plugin room stream that lost its access",
                extra={
                    "plugin_installation_id": str(self.plugin.installation.id),
                    "error": str(exc),
                },
            )
            return False
        if keys != set(self.rooms):
            # A thread's preview moved to another app, whose rooms these are not.
            logger.info(
                "Closed a plugin room stream whose install changed apps",
                extra={"plugin_installation_id": str(self.plugin.installation.id)},
            )
            return False
        return True

    @staticmethod
    def frame(event: str, room: Room, **data: object) -> str:
        payload = {"room": room.name, "repository": room.scope.repository, **data}
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    async def catch_up(self, key: str) -> AsyncGenerator[str]:
        room = self.rooms[key]
        while updates := await room.since(self.seqs[key]):
            for update in updates:
                self.seqs[key] = update.seq
                yield self.frame(
                    "update",
                    room,
                    seq=update.seq,
                    update=base64.b64encode(update.payload).decode(),
                )

    async def events(self) -> AsyncGenerator[str]:
        """Each room's updates after its ``after``, then ``synced``, then everything as it
        commits; it ends once the person may no longer be in every room."""
        loop = asyncio.get_running_loop()
        async with ROOMS.listen(list(self.rooms.values())) as events:
            for key, room in self.rooms.items():
                async for frame in self.catch_up(key):
                    yield frame
                yield self.frame("synced", room, seq=self.seqs[key])
            checked = loop.time()
            while True:
                try:
                    event = await asyncio.wait_for(events.get(), timeout=ROOM_HEARTBEAT_SECONDS)
                except TimeoutError:
                    event = None
                # Access is checked when the stream opens; one that stays open checks again
                # each heartbeat, so revoking it reaches streams already running.
                if loop.time() - checked >= ROOM_HEARTBEAT_SECONDS:
                    if not await self.allowed():
                        return
                    checked = loop.time()
                if event is None:
                    yield ": keep-alive\n\n"
                elif event.room not in self.rooms:
                    continue
                elif event.kind == "presence":
                    yield self.frame(
                        "presence",
                        self.rooms[event.room],
                        client=event.client,
                        presence=event.presence,
                    )
                else:
                    async for frame in self.catch_up(event.room):
                        yield frame


@router.get("/{installation_id}/rooms/stream")
async def api_plugin_rooms_stream(
    plugin: PluginDep,
    rooms: str = Query(description="JSON list of {room, repository, after}"),
) -> StreamingResponse:
    try:
        joins = [RoomJoin.model_validate(item) for item in json.loads(rooms)]
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "rooms must be a JSON list of {room, repository, after}") from exc
    if not 0 < len(joins) <= MAX_ROOMS_PER_STREAM:
        raise HTTPException(422, f"join 1 to {MAX_ROOMS_PER_STREAM} rooms per stream")
    stream = _RoomStream(plugin, joins)
    with _as_http():
        await stream.join()
    return StreamingResponse(stream.events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/{installation_id}/rooms/{room:path}/updates")
async def api_plugin_room_update(
    room: RoomName, body: RoomUpdateBody, plugin: PluginDep
) -> RoomSeq:
    payload = body.payload()
    with _as_http():
        joined = await plugin.room(room, body.repository)
    return RoomSeq(seq=await joined.append(payload, by=plugin.user.id))


@router.post("/{installation_id}/rooms/{room:path}/compact")
async def api_plugin_room_compact(
    room: RoomName, body: RoomCompactBody, plugin: PluginDep
) -> RoomSeq:
    snapshot = body.payload()
    with _as_http():
        joined = await plugin.room(room, body.repository)
    return RoomSeq(seq=await joined.compact(snapshot, through=body.through, by=plugin.user.id))


@router.post("/{installation_id}/rooms/{room:path}/presence", status_code=204)
async def api_plugin_room_presence(
    room: RoomName, body: RoomPresenceBody, plugin: PluginDep
) -> Response:
    with _as_http():
        joined = await plugin.room(room, body.repository)
    await joined.announce(body.client, body.presence)
    return Response(status_code=204)
