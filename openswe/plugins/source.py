"""Where a plugin lives on GitHub, and reading it from there at a pinned commit."""

import logging
from dataclasses import dataclass

import httpx2
from pydantic import ValidationError

from openswe.github.http import GitHubClient, RepoClient, RepoFileUnreadableError
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
MAX_BUNDLE_CHARS = 8_000_000


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
        """The plugin as it is at the commit ``ref`` names now."""
        repo = self.repo(github)
        ref, sha = await self.resolve(repo)
        manifest = await self.manifest(repo, sha)
        html = await _read(repo, self.file(manifest.entry), sha, MAX_BUNDLE_CHARS)
        if html is None:
            raise PluginSourceError(
                f"{self.repository} has no {self.file(manifest.entry)}; "
                "commit the built bundle the manifest names"
            )
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
