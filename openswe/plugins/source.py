"""Where a plugin lives on GitHub, and reading it from there at a pinned commit.

A plugin's UI is built, never committed. The repository's plugin workflow
(``.github/workflows/openswe-plugin.yml``) builds it on each push and keeps the
newest build of each branch or tag as an asset of a release tagged
``openswe-plugin/<name>/<ref>``, named for the commit it was built from. An
install takes that build and pins that commit, reading the manifest there too.
"""

import logging
import re
from dataclasses import dataclass

import httpx2
from pydantic import ValidationError

from openswe.github.http import (
    GitHubClient,
    Release,
    ReleaseAsset,
    RepoClient,
    RepoFileUnreadableError,
)
from openswe.github.repo_names import RepoFullName, normalize_repo_full_name
from openswe.plugins.manifest import (
    MANIFEST_PATH,
    MARKETPLACE_PATH,
    GitHubSource,
    MarketplaceManifest,
    PluginManifest,
)

logger = logging.getLogger(__name__)

MAX_MANIFEST_CHARS = 64_000
MAX_BUNDLE_BYTES = 64_000_000


def release_tag(name: str, ref: str) -> str:
    """The release holding ``name``'s newest build of ``ref``."""
    return f"openswe-plugin/{name}/{ref}"


def newest_build(release: Release, name: str) -> tuple[ReleaseAsset, str] | None:
    """The release's newest ``<name>-<commit>.html`` and the commit it was built from."""
    pattern = re.compile(rf"{re.escape(name)}-([0-9a-f]{{40}})\.html")
    builds = [
        (asset, match.group(1))
        for asset in release.assets
        if (match := pattern.fullmatch(asset.name))
    ]
    if not builds:
        return None
    return max(builds, key=lambda build: build[0].updated_at)


class PluginSourceError(ValueError):
    """A repository holds no installable plugin where one was asked for."""


@dataclass(frozen=True, slots=True)
class PluginSource:
    """A plugin's directory in a GitHub repository; ``ref`` ``None`` is the default branch."""

    repository: RepoFullName
    path: str = ""
    ref: str | None = None

    @classmethod
    def parse(cls, repository: str, path: str = "", ref: str | None = None) -> PluginSource:
        try:
            full_name = normalize_repo_full_name(repository)
        except ValueError as exc:
            raise PluginSourceError("repository must be owner/repo") from exc
        return cls(
            RepoFullName(full_name.lower()), path.strip().strip("/"), (ref or "").strip() or None
        )

    def file(self, name: str) -> str:
        return f"{self.path}/{name}" if self.path else name

    def repo(self, github: GitHubClient) -> RepoClient:
        owner, name = self.repository.split("/", 1)
        return github.repo(owner, name)

    async def resolve(self, repo: RepoClient) -> tuple[str, str]:
        """The ref this source follows and the commit it names now."""
        ref = self.ref or str((await repo.info())["default_branch"])
        return ref, await repo.commit_sha(ref)

    async def manifest(self, repo: RepoClient, sha: str) -> PluginManifest:
        text = await _read(repo, self.file(MANIFEST_PATH), sha, MAX_MANIFEST_CHARS)
        if text is None:
            raise PluginSourceError(f"{self.repository} has no {self.file(MANIFEST_PATH)}")
        try:
            return PluginManifest.model_validate_json(text)
        except ValidationError as exc:
            raise PluginSourceError(f"Invalid {MANIFEST_PATH}: {exc.errors()[0]['msg']}") from exc

    async def fetch(self, github: GitHubClient) -> PluginPackage:
        """The plugin as its newest build of ``ref`` has it, at the commit that build is from."""
        repo = self.repo(github)
        ref, head = await self.resolve(repo)
        name = (await self.manifest(repo, head)).name
        tag = release_tag(name, ref)
        release = await repo.release(tag)
        build = newest_build(release, name) if release is not None else None
        if build is None:
            raise PluginSourceError(
                f"{self.repository} has no build of {name} on {ref} yet. Its plugin workflow "
                f"publishes each build to the {tag} release: push a change to the plugin, "
                "or run the workflow."
            )
        asset, sha = build
        manifest = await self.manifest(repo, sha)
        if manifest.name != name:
            raise PluginSourceError(f"The build at {sha[:7]} is of {manifest.name!r}, not {name!r}")
        if asset.size > MAX_BUNDLE_BYTES:
            raise PluginSourceError(f"{asset.name} is larger than {MAX_BUNDLE_BYTES} bytes")
        try:
            html = (await repo.download_asset(asset)).decode()
        except UnicodeDecodeError as exc:
            raise PluginSourceError(f"{asset.name} is not UTF-8 text") from exc
        if "\0" in html:
            raise PluginSourceError(f"{asset.name} contains NUL characters; escape them as \\u0000")
        return PluginPackage(source=self, ref=ref, commit_sha=sha, manifest=manifest, html=html)


@dataclass(frozen=True, slots=True)
class PluginPackage:
    """A plugin read at one commit: its manifest and its UI bundle."""

    source: PluginSource
    ref: str
    commit_sha: str
    manifest: PluginManifest
    html: str


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    source: PluginSource
    manifest: PluginManifest


async def catalog(github: GitHubClient, source: PluginSource) -> list[CatalogEntry]:
    """The plugins ``source`` offers: itself when it is a plugin, else its marketplace's."""
    repo = source.repo(github)
    _, sha = await source.resolve(repo)
    if await _read(repo, source.file(MANIFEST_PATH), sha, MAX_MANIFEST_CHARS) is not None:
        return [CatalogEntry(source, await source.manifest(repo, sha))]
    text = await _read(repo, source.file(MARKETPLACE_PATH), sha, MAX_MANIFEST_CHARS)
    if text is None:
        raise PluginSourceError(
            f"{source.repository} has neither {MANIFEST_PATH} nor {MARKETPLACE_PATH}"
        )
    try:
        marketplace = MarketplaceManifest.model_validate_json(text)
    except ValidationError as exc:
        raise PluginSourceError(f"Invalid {MARKETPLACE_PATH}: {exc.errors()[0]['msg']}") from exc
    entries: list[CatalogEntry] = []
    for listed in marketplace.plugins:
        match listed.source:
            case str(path):
                plugin = PluginSource(source.repository, source.file(path), source.ref)
            case GitHubSource(repo=repository, ref=ref, sha=pinned):
                plugin = PluginSource.parse(repository, "", pinned or ref)
            case subdir:
                plugin = PluginSource.parse(subdir.url, subdir.path, subdir.sha or subdir.ref)
        try:
            plugin_repo = plugin.repo(github)
            _, plugin_sha = await plugin.resolve(plugin_repo)
            entries.append(CatalogEntry(plugin, await plugin.manifest(plugin_repo, plugin_sha)))
        except (PluginSourceError, httpx2.HTTPError) as exc:
            logger.warning(
                "Skipping unreadable marketplace plugin",
                extra={"plugin_name": listed.name, "plugin_repository": plugin.repository},
                exc_info=exc,
            )
    return entries


async def _read(repo: RepoClient, path: str, sha: str, max_chars: int) -> str | None:
    try:
        return await repo.read_file(path, sha, max_chars=max_chars, strict=True)
    except RepoFileUnreadableError as exc:
        raise PluginSourceError(str(exc)) from exc
