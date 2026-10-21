"""Dashboard API for installing plugins and for the calls their frames relay.

``/plugins`` installs and manages plugins. ``/plugins/{installation_id}/...`` is
everything a running plugin may ask for: its documents, its repositories on
GitHub, and agent threads. The dashboard relays those from the plugin's
sandboxed frame, which cannot reach the network itself.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, StringConstraints

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP, session_is_admin
from openswe.dashboard.oauth import session_user_id
from openswe.github.http import GitHubClient, GitHubError, GitHubSignInRequired, GraphQLError
from openswe.github.repo_names import normalize_repo_full_name
from openswe.plugins.installed import (
    InstalledPlugin,
    PluginPermissionDenied,
    RepositoryNotGranted,
    can_read_repository,
)
from openswe.plugins.manifest import PluginManifest, PluginPermission
from openswe.plugins.records import (
    Installation,
    InstallationNotFound,
    PluginDocument,
    PluginInstallations,
    PluginNameTaken,
)
from openswe.plugins.source import PluginSource, PluginSourceError, catalog
from openswe.threads.handlers import get_dashboard_thread
from openswe.users.models import User
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

MAX_DOCUMENT_BYTES = 256_000
MAX_THREADS_LISTED = 50

CollectionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
DocumentKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:@/-]{1,200}$")]


@contextmanager
def _as_http() -> Iterator[None]:
    """Turn what a plugin call can fail with into the answer its frame sees."""
    try:
        yield
    except InstallationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PluginPermissionDenied, RepositoryNotGranted) as exc:
        raise HTTPException(403, str(exc)) from exc
    except PluginNameTaken as exc:
        raise HTTPException(409, str(exc)) from exc
    except PluginSourceError as exc:
        raise HTTPException(422, str(exc)) from exc
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


async def _managed(installation_id: UUID, viewer: ViewerDep) -> Installation:
    """An install ``viewer`` may change: their own, or everyone's when they are an admin."""
    with _as_http():
        installation = await PluginInstallations.get(installation_id, viewer.user_id)
    if installation.user_id is None and not viewer.admin:
        raise HTTPException(403, "Only admins manage plugins installed for everyone")
    return installation


ManagedDep = Annotated[Installation, Depends(_managed)]


# ---- Installing ------------------------------------------------------------------


class PluginSourceModel(BaseModel):
    repository: str
    path: str
    ref: str | None

    @classmethod
    def of(cls, source: PluginSource) -> PluginSourceModel:
        return cls(repository=source.repository, path=source.path, ref=source.ref)


class PluginSummary(BaseModel):
    id: UUID
    plugin_id: UUID
    """The plugin source's id, shared by every install of it; it keys its documents' topics."""
    name: str
    manifest: PluginManifest
    source: PluginSourceModel
    commit_sha: str
    scope: Literal["user", "organization"]
    repositories: list[str]
    installed_at: datetime | None

    @classmethod
    def of(cls, installation: Installation) -> PluginSummary:
        return cls(
            id=installation.id,
            plugin_id=installation.plugin,
            name=installation.manifest.name,
            manifest=installation.manifest,
            source=PluginSourceModel.of(installation.source),
            commit_sha=installation.commit_sha,
            scope=installation.scope,
            repositories=list(installation.repositories),
            installed_at=installation.created_at,
        )


class CatalogItem(BaseModel):
    manifest: PluginManifest
    source: PluginSourceModel


class InstallBody(BaseModel):
    repository: str
    path: str = ""
    ref: str | None = None
    scope: Literal["user", "organization"] = "user"
    name: str
    """The plugin the installer reviewed; the install fails if the source now holds another."""
    permissions: list[PluginPermission]
    """The permissions the installer approved; the install fails if the plugin asks for more."""
    repositories: list[str] = Field(default_factory=list, max_length=50)


class UpgradeBody(BaseModel):
    permissions: list[PluginPermission]


class RepositoriesBody(BaseModel):
    repositories: list[str] = Field(max_length=50)


async def _grantable(login: str, raw: list[str]) -> list[str]:
    """``raw`` as lowercased ``owner/repo``, each one the installer can reach."""
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
                "message": "The plugin asks for permissions you have not approved",
                "permissions": unapproved,
            },
        )


@router.get("")
async def api_list_plugins(viewer: ViewerDep) -> list[PluginSummary]:
    return [PluginSummary.of(i) for i in await PluginInstallations.visible_to(viewer.user_id)]


@router.get("/catalog")
async def api_plugin_catalog(
    viewer: ViewerDep,
    repository: str = Query(min_length=3, max_length=200),
    path: str = Query("", max_length=200),
    ref: str | None = Query(None, max_length=200),
) -> list[CatalogItem]:
    with _as_http():
        source = PluginSource.parse(repository, path, ref)
        async with GitHubClient.as_user(viewer.login) as github:
            entries = await catalog(github, source)
    return [
        CatalogItem(manifest=entry.manifest, source=PluginSourceModel.of(entry.source))
        for entry in entries
    ]


@router.post("", status_code=201)
@audit_endpoint
async def api_install_plugin(body: InstallBody, viewer: ViewerDep) -> PluginSummary:
    if body.scope == "organization" and not viewer.admin:
        raise HTTPException(403, "Only admins install plugins for everyone")
    repositories = await _grantable(viewer.login, body.repositories)
    with _as_http():
        source = PluginSource.parse(body.repository, body.path, body.ref)
        async with GitHubClient.as_user(viewer.login) as github:
            package = await source.fetch(github)
        if package.manifest.name != body.name:
            raise HTTPException(409, f"{source.repository} now holds {package.manifest.name!r}")
        _check_permissions(package.manifest, body.permissions)
        owner = None if body.scope == "organization" else viewer.user_id
        if owner is not None and await PluginInstallations.named(body.name, None) is not None:
            raise PluginNameTaken(body.name)
        installation_id = await PluginInstallations.install(
            package, user_id=owner, repositories=repositories, by=viewer.user_id
        )
        return PluginSummary.of(await PluginInstallations.get(installation_id, viewer.user_id))


@router.post("/{installation_id}/upgrade")
@audit_endpoint
async def api_upgrade_plugin(
    body: UpgradeBody, installation: ManagedDep, viewer: ViewerDep
) -> PluginSummary:
    """Move the install to the commit its ref names now."""
    with _as_http():
        async with GitHubClient.as_user(viewer.login) as github:
            package = await installation.source.fetch(github)
        if package.manifest.name != installation.manifest.name:
            raise HTTPException(409, f"The source now holds {package.manifest.name!r}")
        _check_permissions(package.manifest, body.permissions)
        await PluginInstallations.upgrade(installation.id, package, by=viewer.user_id)
        return PluginSummary.of(await PluginInstallations.get(installation.id, viewer.user_id))


@router.put("/{installation_id}/repositories")
@audit_endpoint
async def api_set_plugin_repositories(
    body: RepositoriesBody, installation: ManagedDep, viewer: ViewerDep
) -> PluginSummary:
    repositories = await _grantable(viewer.login, body.repositories)
    await PluginInstallations.set_repositories(installation.id, repositories, by=viewer.user_id)
    with _as_http():
        return PluginSummary.of(await PluginInstallations.get(installation.id, viewer.user_id))


@router.delete("/{installation_id}", status_code=204)
@audit_endpoint
async def api_uninstall_plugin(installation: ManagedDep) -> Response:
    await PluginInstallations.remove(installation.id)
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


class DocumentQuery(BaseModel):
    repository: str | None = None
    where: JsonObject = Field(default_factory=dict)
    limit: int = Field(200, ge=1, le=500)


class DocumentWrite(BaseModel):
    repository: str | None = None
    document: JsonObject


@router.post("/{installation_id}/documents/{collection}/query")
async def api_query_plugin_documents(
    collection: CollectionName, body: DocumentQuery, plugin: PluginDep
) -> list[DocumentModel]:
    with _as_http():
        scope = await plugin.document_scope(body.repository)
        found = await plugin.documents.query(collection, scope, body.where, limit=body.limit)
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
    sha: str


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
            return RefResponse(sha=await client.commit_sha(name))


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


# ---- Threads -----------------------------------------------------------------------


class ThreadBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    request: str = Field(min_length=1, max_length=40_000)
    repository: str


class ThreadStarted(BaseModel):
    thread_id: str


class PluginThread(BaseModel):
    thread_id: str
    title: str
    status: str
    updated_at_ms: int | None


@router.post("/{installation_id}/threads", status_code=201)
@audit_endpoint
async def api_plugin_start_thread(body: ThreadBody, plugin: PluginDep) -> ThreadStarted:
    with _as_http():
        thread_id = await plugin.start_thread(
            title=body.title, request=body.request, repository=body.repository
        )
    return ThreadStarted(thread_id=thread_id)


@router.get("/{installation_id}/threads")
async def api_plugin_threads(plugin: PluginDep) -> list[PluginThread]:
    with _as_http():
        plugin.require(PluginPermission.THREADS)
    threads: list[PluginThread] = []
    for thread_id in await plugin.threads.started_by(plugin.user_id, limit=MAX_THREADS_LISTED):
        try:
            thread = await get_dashboard_thread(
                thread_id, plugin.login, email=plugin.email, mark_viewed=False
            )
        except HTTPException as exc:
            logger.info(
                "Skipping a plugin thread the person can no longer read",
                extra={"agent_thread_id": thread_id, "status_code": exc.status_code},
            )
            continue
        threads.append(
            PluginThread(
                thread_id=thread_id,
                title=str(thread.get("title") or ""),
                status=str(thread.get("status") or "idle"),
                updated_at_ms=thread.get("updatedAt"),
            )
        )
    return threads
