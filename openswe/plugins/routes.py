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
from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP, session_is_admin
from openswe.dashboard.oauth import session_user_id
from openswe.github.http import GitHubError, GitHubSignInRequired, GraphQLError
from openswe.github.repo_names import normalize_repo_full_name
from openswe.plugins.installed import (
    InstalledPlugin,
    PluginChatNotFound,
    PluginPermissionDenied,
    RepositoryNotGranted,
    can_read_repository,
)
from openswe.plugins.manifest import MAX_BUNDLE_BYTES, BundleError, PluginManifest, PluginPermission
from openswe.plugins.records import (
    AppNotFound,
    AppVersion,
    Cursor,
    DocumentQuery,
    Installation,
    InstallationNotFound,
    PluginApps,
    PluginChatCall,
    PluginDocument,
    PluginInstallations,
    PluginNameTaken,
    tool_prefix,
)
from openswe.plugins.rooms import MAX_PRESENCE_BYTES, Room, listen
from openswe.users.models import User
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

MAX_DOCUMENT_BYTES = 256_000

CollectionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
DocumentKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:@/-]{1,200}$")]
FieldPath = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}(\.[A-Za-z0-9_-]{1,64}){0,7}$")
]


@contextmanager
def _as_http() -> Iterator[None]:
    """Turn what a plugin call can fail with into the answer its frame sees."""
    try:
        yield
    except InstallationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PluginPermissionDenied, RepositoryNotGranted) as exc:
        raise HTTPException(403, str(exc)) from exc
    except PluginChatNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PluginNameTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    except BundleError as exc:
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


class _Viewer(BaseModel):
    login: str
    email: str | None
    user_id: UUID
    admin: bool


async def _viewer(session: dict[str, Any] = SESSION_DEP) -> _Viewer:
    user = await User.for_session(session_user_id(session), session["sub"])
    if user is None:
        raise HTTPException(409, "No Open SWE user record for this login yet")
    return _Viewer(
        login=session["sub"],
        email=session.get("email"),
        user_id=user.id,
        admin=session_is_admin(session),
    )


ViewerDep = Annotated[_Viewer, Depends(_viewer)]


async def _installed(installation_id: UUID, viewer: ViewerDep) -> InstalledPlugin:
    with _as_http():
        installation = await PluginInstallations.get(installation_id, viewer.user_id)
    return InstalledPlugin(
        installation=installation, login=viewer.login, email=viewer.email, user_id=viewer.user_id
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


class PreviewModel(BaseModel):
    running: PluginSummary | None
    """The build running beside the thread."""
    waiting: AppVersionModel | None
    """A newer build that asks for permissions the viewer has not approved yet."""


class InstallBody(BaseModel):
    version: UUID
    permissions: list[PluginPermission]
    """The permissions the person approved; it fails if the version asks for more."""


class ListedBody(BaseModel):
    listed: bool


class RepositoriesBody(BaseModel):
    repositories: list[str] = Field(min_length=1, max_length=50)


async def _shareable(login: str, raw: list[str]) -> list[str]:
    """``raw`` as lowercased ``owner/repo``, each one ``login`` can read."""
    try:
        names = sorted({normalize_repo_full_name(repo).lower() for repo in raw})
    except ValueError as exc:
        raise HTTPException(422, "repositories must be owner/repo") from exc
    readable = await asyncio.gather(*(can_read_repository(login, name) for name in names))
    if missing := [name for name, ok in zip(names, readable, strict=True) if not ok]:
        raise HTTPException(403, f"You cannot reach {', '.join(missing)}")
    return names


def _check_permissions(manifest: PluginManifest, approved: list[PluginPermission]) -> None:
    if unapproved := sorted(set(manifest.permissions) - set(approved)):
        raise HTTPException(
            409,
            {
                "message": "The app asks for permissions you have not approved",
                "permissions": unapproved,
            },
        )


async def _summary(installation_id: UUID, viewer: _Viewer) -> PluginSummary:
    with _as_http():
        return PluginSummary.of(await PluginInstallations.get(installation_id, viewer.user_id))


@router.get("")
async def api_list_plugins(viewer: ViewerDep) -> list[PluginSummary]:
    return [PluginSummary.of(i) for i in await PluginInstallations.visible_to(viewer.user_id)]


@router.get("/apps")
async def api_list_apps(viewer: ViewerDep) -> list[AppModel]:
    """The apps the viewer may add: their own, and the ones others listed."""
    return [
        AppModel(
            id=app.id,
            name=app.name,
            mine=app.owner == viewer.user_id,
            listed=app.listed,
            latest=AppVersionModel.of(app.latest) if app.latest else None,
        )
        for app in await PluginApps.available_to(viewer.user_id)
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
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BUNDLE_BYTES:
        raise HTTPException(413, f"an app is at most {MAX_BUNDLE_BYTES} bytes")
    try:
        html = (await request.body()).decode()
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "the bundle must be UTF-8 HTML") from exc
    origin: JsonObject = {
        key: value
        for key, value in {"repository": repository, "ref": ref, "commit": commit}.items()
        if value
    }
    with _as_http():
        return AppVersionModel.of(await PluginApps.publish(viewer.user_id, html, origin=origin))


@router.patch("/apps/{app_id}", status_code=204)
@audit_endpoint
async def api_list_app(app_id: UUID, body: ListedBody, viewer: ViewerDep) -> Response:
    """List the app for anyone to add, or keep it to the viewer, who publishes it."""
    with _as_http():
        await PluginApps.set_listed(app_id, viewer.user_id, body.listed)
    return Response(status_code=204)


@router.post("", status_code=201)
@audit_endpoint
async def api_install_plugin(body: InstallBody, viewer: ViewerDep) -> PluginSummary:
    """Add a version of an app to the viewer's web UI."""
    with _as_http():
        version = await PluginApps.version(body.version, viewer.user_id)
        _check_permissions(version.manifest, body.permissions)
        installation_id = await PluginInstallations.install(version, user_id=viewer.user_id)
    return await _summary(installation_id, viewer)


@router.get("/previews/{thread_id}")
async def api_app_preview(thread_id: str, viewer: ViewerDep) -> PreviewModel:
    """The app the viewer's agent builds in ``thread_id``, as it runs beside the thread."""
    running = await PluginInstallations.previewing(thread_id, viewer.user_id)
    newest = await PluginApps.newest_preview(thread_id, viewer.user_id)
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
    newest = await PluginApps.newest_preview(thread_id, viewer.user_id)
    if newest is None or newest.id != body.version:
        raise HTTPException(409, "The agent has built a newer preview since")
    _check_permissions(newest.manifest, body.permissions)
    await PluginInstallations.preview(
        thread_id, newest, user_id=viewer.user_id, approved=body.permissions
    )
    return await api_app_preview(thread_id, viewer)


@router.post("/previews/{thread_id}/release", status_code=201)
@audit_endpoint
async def api_release_app_preview(thread_id: str, viewer: ViewerDep) -> PluginSummary:
    """Keep the build running beside the thread: release it, in the viewer's sidebar."""
    running = await PluginInstallations.previewing(thread_id, viewer.user_id)
    if running is None or running.owner != viewer.user_id:
        raise HTTPException(404, "No preview runs beside this thread")
    with _as_http():
        version = await PluginApps.release(running.version, by=viewer.user_id)
        installed = next(
            (
                installation
                for installation in await PluginInstallations.visible_to(viewer.user_id)
                if installation.plugin == version.app
            ),
            None,
        )
        if installed is None:
            installation_id = await PluginInstallations.install(version, user_id=viewer.user_id)
        else:
            installation_id = installed.id
            await PluginInstallations.upgrade(installed.id, version, by=viewer.user_id)
    return await _summary(installation_id, viewer)


@router.post("/{installation_id}/upgrade")
@audit_endpoint
async def api_upgrade_plugin(
    body: InstallBody, plugin: PluginDep, viewer: ViewerDep
) -> PluginSummary:
    """Move the install to another version of its app."""
    with _as_http():
        version = await PluginApps.version(body.version, viewer.user_id)
        if version.app != plugin.installation.plugin or version.preview_of is not None:
            raise HTTPException(409, "That is not a release of this app")
        _check_permissions(version.manifest, body.permissions)
        await PluginInstallations.upgrade(plugin.installation.id, version, by=viewer.user_id)
    return await _summary(plugin.installation.id, viewer)


@router.post("/{installation_id}/repositories")
@audit_endpoint
async def api_share_plugin_repositories(
    body: RepositoriesBody, plugin: PluginDep, viewer: ViewerDep
) -> PluginSummary:
    """Share repositories with the app, for the viewer only: what they picked when it asked."""
    repositories = await _shareable(viewer.login, body.repositories)
    await PluginInstallations.share(plugin.installation.id, viewer.user_id, repositories)
    return await _summary(plugin.installation.id, viewer)


@router.delete("/{installation_id}/repositories/{owner}/{repo}")
@audit_endpoint
async def api_unshare_plugin_repository(
    owner: str, repo: str, plugin: PluginDep, viewer: ViewerDep
) -> PluginSummary:
    await PluginInstallations.unshare(
        plugin.installation.id, viewer.user_id, f"{owner}/{repo}".lower()
    )
    return await _summary(plugin.installation.id, viewer)


@router.delete("/{installation_id}", status_code=204)
@audit_endpoint
async def api_uninstall_plugin(plugin: PluginDep, viewer: ViewerDep) -> Response:
    await PluginInstallations.remove(plugin.installation.id, viewer.user_id)
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
) -> DocumentModel:
    if len(body.model_dump_json()) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, "document too large")
    with _as_http():
        scope = await plugin.document_scope(body.repository)
        document = await plugin.documents.put(
            collection, scope, key, body.document, by=plugin.user_id
        )
    return DocumentModel.of(document)


@router.delete("/{installation_id}/documents/{collection}/{key:path}", status_code=204)
async def api_delete_plugin_document(
    collection: CollectionName,
    key: DocumentKey,
    plugin: PluginDep,
    repository: str | None = None,
) -> Response:
    with _as_http():
        scope = await plugin.document_scope(repository)
        if not await plugin.documents.delete(collection, scope, key):
            raise HTTPException(404, "document not found")
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


class HistoryEntry(BaseModel):
    sha: str
    message: str
    author_name: str
    author_login: str | None
    authored_at: str | None


class BranchBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class CommitBody(BaseModel):
    branch: str = Field(min_length=1, max_length=200)
    expected_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    message: str = Field(min_length=1, max_length=10_000)
    files: dict[str, str] = Field(default_factory=dict, max_length=100)
    deletions: list[str] = Field(default_factory=list, max_length=100)


class PullRequestModel(BaseModel):
    number: int
    title: str
    url: str
    state: str
    draft: bool
    head: str
    base: str

    @classmethod
    def of(cls, payload: dict[str, Any]) -> PullRequestModel:
        return cls(
            number=payload["number"],
            title=payload.get("title") or "",
            url=payload.get("html_url") or "",
            state=payload.get("state") or "open",
            draft=bool(payload.get("draft")),
            head=(payload.get("head") or {}).get("ref") or "",
            base=(payload.get("base") or {}).get("ref") or "",
        )


class PullRequestBody(BaseModel):
    head: str = Field(min_length=1, max_length=200)
    base: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=60_000)
    draft: bool = False


def _repository(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


@router.get("/{installation_id}/github/{owner}/{repo}")
async def api_plugin_repository(owner: str, repo: str, plugin: PluginDep) -> RepositoryInfo:
    with _as_http():
        async with plugin.github(_repository(owner, repo)) as client:
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
        async with plugin.github(_repository(owner, repo)) as client:
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
        async with plugin.github(_repository(owner, repo)) as client:
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
    path: str = Query(min_length=1, max_length=1000),
    ref: str = Query(min_length=1, max_length=200),
) -> FileResponse:
    with _as_http():
        async with plugin.github(_repository(owner, repo)) as client:
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
    path: str = Query(min_length=1, max_length=1000),
    ref: str = Query(min_length=1, max_length=200),
) -> Response:
    """An image in the repository, as bytes the plugin's frame can show; its own policy loads none."""
    media = IMAGE_TYPES.get(PurePosixPath(path).suffix.lower())
    if media is None:
        raise HTTPException(415, "not an image")
    with _as_http():
        async with plugin.github(_repository(owner, repo)) as client:
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
    path: str = Query(min_length=1, max_length=1000),
    ref: str = Query(min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
) -> list[HistoryEntry]:
    with _as_http():
        async with plugin.github(_repository(owner, repo)) as client:
            commits = await client.file_history(path, ref, limit=limit)
    return [
        HistoryEntry(
            sha=commit["sha"],
            message=(commit.get("commit") or {}).get("message") or "",
            author_name=((commit.get("commit") or {}).get("author") or {}).get("name") or "",
            author_login=(commit.get("author") or {}).get("login"),
            authored_at=((commit.get("commit") or {}).get("author") or {}).get("date"),
        )
        for commit in commits
    ]


@router.post("/{installation_id}/github/{owner}/{repo}/branches", status_code=204)
async def api_plugin_create_branch(
    owner: str, repo: str, body: BranchBody, plugin: PluginDep
) -> Response:
    with _as_http():
        async with plugin.github(_repository(owner, repo), write=True) as client:
            await client.create_branch(body.name, body.sha)
    return Response(status_code=204)


@router.delete("/{installation_id}/github/{owner}/{repo}/branches/{name:path}", status_code=204)
@audit_endpoint
async def api_plugin_delete_branch(owner: str, repo: str, name: str, plugin: PluginDep) -> Response:
    """Delete a branch, such as a draft's; never the repository's default branch."""
    with _as_http():
        async with plugin.github(_repository(owner, repo), write=True) as client:
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
        async with plugin.github(_repository(owner, repo), write=True) as client:
            sha = await client.commit_files(
                branch=body.branch,
                expected_head=body.expected_head,
                message=body.message,
                additions=body.files,
                deletions=body.deletions,
            )
    return RefResponse(sha=sha)


@router.get("/{installation_id}/github/{owner}/{repo}/pulls")
async def api_plugin_pull_for_branch(
    owner: str, repo: str, plugin: PluginDep, head: str = Query(min_length=1, max_length=200)
) -> PullRequestModel | None:
    with _as_http():
        async with plugin.github(_repository(owner, repo)) as client:
            pull = await client.open_pull_for_branch(head)
    return None if pull is None else PullRequestModel.of(pull)


@router.post("/{installation_id}/github/{owner}/{repo}/pulls", status_code=201)
@audit_endpoint
async def api_plugin_open_pull(
    owner: str, repo: str, body: PullRequestBody, plugin: PluginDep
) -> PullRequestModel:
    with _as_http():
        async with plugin.github(_repository(owner, repo), write=True) as client:
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
    return _calls(plugin, await chat.calls.pending())


@router.post("/{installation_id}/chats/{chat_id}/calls/claim")
async def api_claim_plugin_chat_calls(chat_id: UUID, plugin: PluginDep) -> list[ChatCallModel]:
    """Take the waiting calls to run them in this dock's frame, so no other open dock runs them too."""
    with _as_http():
        chat = await plugin.own_chat(chat_id)
    return _calls(plugin, await chat.calls.claim())


def _calls(plugin: InstalledPlugin, calls: Sequence[PluginChatCall]) -> list[ChatCallModel]:
    prefix = tool_prefix(plugin.installation.manifest.name)
    return [
        ChatCallModel(
            call_id=call.call_id, tool=call.tool.removeprefix(prefix), arguments=call.arguments
        )
        for call in calls
    ]


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


class RoomUpdateBody(BaseModel):
    repository: str | None = None
    update: str = Field(description="Base64 of the update's bytes")


class RoomCompactBody(BaseModel):
    repository: str | None = None
    snapshot: str = Field(description="Base64 of a state covering every update through `through`")
    through: int = Field(ge=0)


class RoomPresenceBody(BaseModel):
    repository: str | None = None
    client: str = Field(min_length=1, max_length=64)
    presence: str = Field(max_length=MAX_PRESENCE_BYTES, description="Base64 presence state")


class RoomSeq(BaseModel):
    seq: int


def _decoded(value: str, limit: int) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise HTTPException(422, "payload must be base64") from exc
    if len(data) > limit:
        raise HTTPException(413, "payload too large")
    return data


def _room_frame(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


MAX_ROOMS_PER_STREAM = 20


class RoomJoin(BaseModel):
    room: RoomName
    repository: str | None = None
    after: int = Field(0, ge=0)


def _room_names(room: Room) -> dict[str, object]:
    return {"room": room.name, "repository": room.scope.repository}


async def _rooms_stream(joined: list[tuple[Room, int]]) -> AsyncGenerator[str]:
    """Each room's updates after its ``after``, then ``synced``, then everything as it commits.

    One stream carries every room a frame has joined: browsers allow a host only
    a handful of HTTP/1.1 connections, and a page joins several rooms.
    """
    rooms = {room.key: room for room, _ in joined}
    seqs = {room.key: after for room, after in joined}

    async def catch_up(key: str) -> AsyncGenerator[str]:
        room = rooms[key]
        while updates := await room.since(seqs[key]):
            for update in updates:
                seqs[key] = update.seq
                yield _room_frame(
                    "update",
                    {
                        **_room_names(room),
                        "seq": update.seq,
                        "update": base64.b64encode(update.payload).decode(),
                    },
                )

    async with listen(list(rooms.values())) as events:
        for key in rooms:
            async for frame in catch_up(key):
                yield frame
            yield _room_frame("synced", {**_room_names(rooms[key]), "seq": seqs[key]})
        while True:
            try:
                event = await asyncio.wait_for(events.get(), timeout=ROOM_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event.room not in rooms:
                continue
            if event.kind == "presence":
                yield _room_frame(
                    "presence",
                    {
                        **_room_names(rooms[event.room]),
                        "client": event.client,
                        "presence": event.presence,
                    },
                )
            else:
                async for frame in catch_up(event.room):
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
    with _as_http():
        joined = [(await plugin.room(join.room, join.repository), join.after) for join in joins]
    return StreamingResponse(
        _rooms_stream(joined), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.post("/{installation_id}/rooms/{room:path}/updates")
async def api_plugin_room_update(
    room: RoomName, body: RoomUpdateBody, plugin: PluginDep
) -> RoomSeq:
    payload = _decoded(body.update, MAX_UPDATE_BYTES)
    with _as_http():
        joined = await plugin.room(room, body.repository)
    return RoomSeq(seq=await joined.append(payload, by=plugin.user_id))


@router.post("/{installation_id}/rooms/{room:path}/compact")
async def api_plugin_room_compact(
    room: RoomName, body: RoomCompactBody, plugin: PluginDep
) -> RoomSeq:
    snapshot = _decoded(body.snapshot, MAX_SNAPSHOT_BYTES)
    with _as_http():
        joined = await plugin.room(room, body.repository)
    return RoomSeq(seq=await joined.compact(snapshot, through=body.through, by=plugin.user_id))


@router.post("/{installation_id}/rooms/{room:path}/presence", status_code=204)
async def api_plugin_room_presence(
    room: RoomName, body: RoomPresenceBody, plugin: PluginDep
) -> Response:
    with _as_http():
        joined = await plugin.room(room, body.repository)
    await joined.announce(body.client, body.presence)
    return Response(status_code=204)
