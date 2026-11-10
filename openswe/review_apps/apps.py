"""The apps a pull request's review does work for, and what it may write for them.

An app's manifest can declare a review step (``ReviewStep``): instructions, the
app-written collections the review may write, and whether it needs a browser. A
workspace adds apps to its reviews (``review_apps``). A review of a private repository,
whose preview runs only code its organization's members could push, then follows each
app's instructions, and writes only that app's declared collections, in the reviewed
repository's scope, under ``pulls/<number>/``: never another pull request's data.
"""

import asyncio
import json
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, replace
from io import BytesIO
from typing import ClassVar, Self
from uuid import UUID, uuid7

from PIL import Image
from pydantic import TypeAdapter

from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_names import normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.plugins.manifest import ReviewStep
from openswe.plugins.records import (
    ATTACHMENT_TYPES,
    MAX_ATTACHMENT_BYTES,
    MAX_DOCUMENT_BYTES,
    AppVersion,
    AttachmentName,
    DocumentKey,
    DocumentScope,
    PluginApps,
    PluginDocuments,
)
from openswe.run_config import RunConfig
from openswe.sandboxes.state import get_sandbox_backend
from openswe.utils.json_types import JsonObject

# Where a review's browser saves the screenshots run code takes, in the run's sandbox.
WORKSPACE = "/workspace/"
MAX_FILES = 40
# Past this, decoding a PNG to re-encode it costs more memory than the space is worth.
MAX_RECODED_PIXELS = 16_000_000
_KEY = TypeAdapter(DocumentKey)
_NAME = TypeAdapter(AttachmentName)


@dataclass(frozen=True, slots=True)
class SandboxFile:
    """A file a review saved in its sandbox, of a type an app may keep."""

    content: bytes
    content_type: str

    @staticmethod
    def checked_path(path: str) -> str:
        """``path`` when it names a file under the sandbox's workspace."""
        if not path.startswith(WORKSPACE) or posixpath.normpath(path) != path:
            raise ValueError(f"{path}: a file is an absolute path under {WORKSPACE}")
        return path

    @classmethod
    def of(cls, path: str, content: bytes) -> Self:
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"{path}: a file is at most 8 MiB")
        content_type = cls._sniffed(content)
        if content_type not in ATTACHMENT_TYPES:
            raise ValueError(f"{path}: a file is a PNG, JPEG, WebP or GIF image, or a WebM or MP4")
        return cls(content, content_type)

    @classmethod
    async def read(cls, thread_id: str, paths: list[str]) -> dict[str, Self]:
        """The files at ``paths`` in the thread's sandbox."""
        unique = list(dict.fromkeys(cls.checked_path(path) for path in paths))
        if not unique:
            return {}
        backend = await get_sandbox_backend(thread_id)
        found: dict[str, Self] = {}
        for path, download in zip(unique, await backend.adownload_files(unique), strict=True):
            if download.content is None:
                raise ValueError(f"could not read {path}: {download.error}")
            found[path] = cls.of(path, download.content)
        return found

    @classmethod
    async def write(cls, thread_id: str, files: Mapping[str, bytes]) -> None:
        """Saves ``files`` by path in the thread's sandbox, beside files already there."""
        paths = [cls.checked_path(path) for path in files]
        backend = await get_sandbox_backend(thread_id)
        for result in await backend.aupload_files([(path, files[path]) for path in paths]):
            if result.error:
                raise ValueError(f"could not save {result.path}: {result.error}")

    def kept(self) -> Self:
        """This file as an app keeps it: a PNG becomes a lossless WebP, often a quarter of the
        size with every pixel unchanged, unless that is no smaller or would lose something."""
        header = self.content[12:25]
        if (
            self.content_type != "image/png"
            or len(header) < 13
            or header[:4] != b"IHDR"
            # WebP keeps 8 bits a channel, so a 16-bit PNG stays as it is.
            or header[12] > 8
        ):
            return self
        try:
            with Image.open(BytesIO(self.content)) as image:
                if (
                    getattr(image, "is_animated", False)
                    or image.width * image.height > MAX_RECODED_PIXELS
                ):
                    return self
                output = BytesIO()
                image.save(
                    output,
                    format="WEBP",
                    lossless=True,
                    exact=True,
                    icc_profile=image.info.get("icc_profile"),
                )
        except OSError, ValueError, Image.DecompressionBombError:
            return self
        webp = output.getvalue()
        if len(webp) >= len(self.content):
            return self
        return replace(self, content=webp, content_type="image/webp")

    @staticmethod
    def _sniffed(content: bytes) -> str | None:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        if content.startswith(b"\x1a\x45\xdf\xa3"):
            return "video/webm"
        if content[4:8] == b"ftyp":
            return "video/mp4"
        return None


@dataclass(frozen=True, slots=True)
class ReviewApp:
    """An app a workspace added to its reviews, as the release an admin approved describes it."""

    id: UUID
    release: UUID
    name: str
    display_name: str
    step: ReviewStep

    @classmethod
    def of(cls, release: AppVersion) -> Self | None:
        """The app as ``release`` has reviews work for it; None when it has no review step."""
        step = release.manifest.review
        if step is None:
            return None
        manifest = release.manifest
        return cls(release.app, release.id, manifest.name, manifest.display_name, step)

    @staticmethod
    async def describe(pinned: Mapping[UUID, UUID]) -> list[JsonObject]:
        """Each app as an admin reads it: the review step of the release they approved, and
        the number of a newer release, which reviews follow only once an admin adds it."""
        approved = {release.app: release for release in await PluginApps.releases(pinned)}
        described: list[JsonObject] = []
        for app, release_id in pinned.items():
            release = approved.get(app)
            newest = await PluginApps.newest_release(app)
            step = release.manifest.review if release is not None else None
            newer = (
                newest
                if newest is not None
                and newest.manifest.review is not None
                and (release is None or newest.number > release.number)
                else None
            )
            described.append(
                {
                    "id": str(app),
                    "release": str(release_id),
                    "name": release.manifest.name if release is not None else None,
                    "release_number": release.number if release is not None else None,
                    "review_step": step is not None,
                    "browser": bool(step and step.browser),
                    "writes": list(step.writes) if step else [],
                    "newer_release_number": newer.number if newer is not None else None,
                }
            )
        return described


@dataclass(frozen=True, slots=True)
class ReviewApps:
    """The apps one review does work for, and the pull request it reviews."""

    apps: tuple[ReviewApp, ...]
    repository: str
    number: int
    # Saves still running, held so a cancelled call's save is not collected mid-write.
    _saving: ClassVar[set[asyncio.Task[str]]] = set()

    @classmethod
    async def of(cls, cfg: RunConfig) -> Self | None:
        """The apps this review works for; None when it works for none."""
        if cfg.is_eval or cfg.repo is None or cfg.pr_number is None or not cfg.head_sha:
            return None
        pinned = (await cached_workspace_settings(cfg.workspace_slug)).review_app_releases
        if not pinned:
            return None
        name = f"{cfg.repo.owner}/{cfg.repo.name}"
        repository = await Repository.get(name)
        if repository is None or repository.private is not True:
            return None
        apps = tuple(
            app
            for release in await PluginApps.releases(pinned)
            if (app := ReviewApp.of(release)) is not None
        )
        if not apps:
            return None
        return cls(apps, normalize_repo_full_name(name).lower(), cfg.pr_number)

    @property
    def browses(self) -> bool:
        return any(app.step.browser for app in self.apps)

    @property
    def writes(self) -> bool:
        return any(app.step.writes for app in self.apps)

    def app(self, app_id: str) -> ReviewApp:
        """The app by id, since names can repeat across publishers."""
        for app in self.apps:
            if str(app.id) == app_id.strip().lower():
                return app
        works_for = ", ".join(f"{app.name} ({app.id})" for app in self.apps)
        raise ValueError(f"this review works for {works_for}, not {app_id!r}")

    def key(self, key: str) -> str:
        """``key`` under this pull request's ``pulls/<number>/``; one ending in ``/`` gets a
        time-ordered id, so the newest of a prefix reads first in descending key order."""
        prefix = f"pulls/{self.number}/"
        if not key.startswith(prefix):
            raise ValueError(f"a review writes only keys under {prefix}")
        return _KEY.validate_python(f"{key}{uuid7()}" if key.endswith("/") else key)

    async def save(
        self,
        app_id: str,
        collection: str,
        key: str,
        document: JsonObject,
        files: Mapping[str, SandboxFile],
    ) -> str:
        """Writes ``document``, then ``files`` as its attachments; returns its key.

        A cancelled call leaves the save to finish, so its document never lacks its files
        for a reason the review could not see.
        """
        app = self.app(app_id)
        if collection not in app.step.writes:
            raise ValueError(f"{app.name} has reviews write only {', '.join(app.step.writes)}")
        stored_key = self.key(key)
        if len(json.dumps(document).encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError("a document is at most 256 kB")
        if len(files) > MAX_FILES:
            raise ValueError(f"a document has at most {MAX_FILES} files")
        names = {_NAME.validate_python(name): file for name, file in files.items()}
        kept = await asyncio.gather(*(asyncio.to_thread(file.kept) for file in names.values()))
        task = asyncio.ensure_future(
            self._write(
                app.id, collection, stored_key, document, dict(zip(names, kept, strict=True))
            )
        )
        ReviewApps._saving.add(task)
        task.add_done_callback(ReviewApps._saving.discard)
        return await asyncio.shield(task)

    async def _write(
        self,
        app: UUID,
        collection: str,
        key: str,
        document: JsonObject,
        files: Mapping[str, SandboxFile],
    ) -> str:
        documents = PluginDocuments(app)
        scope = DocumentScope(repository=self.repository)
        await documents.put(collection, scope, key, document, by=None)
        try:
            for name, file in files.items():
                attached = await documents.attach(
                    collection, scope, key, name, file.content_type, file.content, by=None
                )
                if attached is None:
                    raise ValueError(f"{key} was deleted while its files were saved")
        except BaseException:
            # A document never stays without the files it names.
            await documents.delete(collection, scope, key)
            raise
        if await self._closed():
            # Closing deleted what reviews saved for the pull request before this landed.
            await documents.delete(collection, scope, key)
            raise ValueError("the pull request closed, so nothing was kept for it")
        return key

    async def _closed(self) -> bool:
        owner, _, repo = self.repository.partition("/")
        stored = await PullRequest.get(owner, repo, self.number)
        return stored is not None and stored.state in ("closed", "merged")
