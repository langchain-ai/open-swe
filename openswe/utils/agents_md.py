"""Fetch ``AGENTS.md`` (or ``CLAUDE.md`` fallback) from a GitHub repo.

Used by the reviewer to deterministically load repo conventions into context
without the model having to clone the repo and read the file itself.
"""

import json
import logging
import posixpath
import shlex
from collections.abc import Iterable

import httpx2
from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)

# Cap the inlined content. AGENTS.md / CLAUDE.md is meant to be a short
# conventions doc; anything larger is probably accidental and would bloat
# every reviewer prompt.
_MAX_AGENTS_MD_BYTES = 64 * 1024

# Filenames tried in order of preference. AGENTS.md is the cross-tool standard;
# CLAUDE.md is the legacy Anthropic-specific filename still used by many repos.
_AGENT_DOC_FILENAMES = ("AGENTS.md", "CLAUDE.md")


def applicable_agents_md_paths(changed_files: Iterable[str]) -> list[str]:
    """Return repo-relative ancestor ``AGENTS.md`` paths for changed files.

    The root file is loaded separately (with the ``CLAUDE.md`` fallback), so
    this only returns directory-scoped files. Paths are ordered from shallowest
    to deepest so later instructions can take precedence in the prompt.
    """
    candidates: set[str] = set()
    for raw_path in changed_files:
        path = posixpath.normpath(raw_path.strip())
        if not raw_path.strip() or path in {".", ".."} or path.startswith(("/", "../")):
            continue
        parts = path.split("/")
        for depth in range(1, len(parts)):
            candidates.add("/".join((*parts[:depth], "AGENTS.md")))
    return sorted(candidates, key=lambda path: (path.count("/"), path))


async def fetch_agents_md(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: str | None,
    timeout: float = 10.0,
) -> str | None:
    """Fetch ``AGENTS.md`` (or ``CLAUDE.md`` fallback) at ``ref`` from ``owner/repo``.

    Returns the raw file contents of the first matching file, or ``None`` if no
    file is found, a fetch fails, or the file exceeds the size cap. Only a 404
    (file absent) triggers fallback to the next filename; any other condition
    (oversize, HTTP error, unexpected status) returns ``None`` immediately so
    the reviewer does not enforce stale rules from a secondary file.
    """
    if not owner or not repo or not ref:
        return None

    headers = {
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx2.AsyncClient(transport=pooled_transport(), timeout=timeout) as client:
        for filename in _AGENT_DOC_FILENAMES:
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{filename}"
            try:
                response = await client.get(url, headers=headers, params={"ref": ref})
            except httpx2.HTTPError:
                logger.exception("Failed to fetch %s from %s/%s@%s", filename, owner, repo, ref)
                return None

            if response.status_code == 404:
                continue
            if response.status_code != 200:
                logger.warning(
                    "Unexpected status %s fetching %s from %s/%s@%s",
                    response.status_code,
                    filename,
                    owner,
                    repo,
                    ref,
                )
                return None

            content = response.text
            if len(content.encode("utf-8")) > _MAX_AGENTS_MD_BYTES:
                logger.info(
                    "%s in %s/%s@%s exceeds %d bytes; skipping inline",
                    filename,
                    owner,
                    repo,
                    ref,
                    _MAX_AGENTS_MD_BYTES,
                )
                return None

            logger.info(
                "Loaded %s (%d chars) from %s/%s@%s",
                filename,
                len(content),
                owner,
                repo,
                ref,
            )
            return content

    return None


_READ_SCOPED_DOCS = """
import json, subprocess, sys
ref, paths, limit = json.loads(sys.argv[1])
tree = subprocess.check_output(["git", "ls-tree", "-rz", ref], stderr=subprocess.PIPE)
blobs = {}
for entry in tree.split(b"\\0"):
    if not entry:
        continue
    metadata, path = entry.split(b"\\t", 1)
    mode, kind, oid = metadata.split()
    if kind == b"blob" and mode != b"120000":
        blobs[path.decode("utf-8")] = oid.decode("ascii")
result = {}
for path in paths:
    if path not in blobs:
        continue
    content = subprocess.check_output(["git", "cat-file", "blob", blobs[path]], stderr=subprocess.PIPE)
    result[path] = content.decode("utf-8") if len(content) <= limit else None
print(json.dumps(result))
"""


async def fetch_scoped_agents_md(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    ref: str,
    changed_files: Iterable[str],
) -> dict[str, str]:
    """Read applicable scoped instructions from the clone's trusted base tree."""
    if not ref:
        return {}
    directories = [posixpath.dirname(path) for path in applicable_agents_md_paths(changed_files)]
    if not directories:
        return {}
    paths = [posixpath.join(d, name) for d in directories for name in _AGENT_DOC_FILENAMES]
    args = json.dumps([ref, paths, _MAX_AGENTS_MD_BYTES])
    command = (
        f"cd {shlex.quote(repo_dir)} && "
        f"python3 -c {shlex.quote(_READ_SCOPED_DOCS)} {shlex.quote(args)}"
    )
    try:
        response = await sandbox_backend.aexecute(command)
        if response.exit_code != 0:
            logger.warning(
                "Failed to read scoped agent docs", extra={"ref": ref, "output": response.output}
            )
            return {}
        files = json.loads(response.output)
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and (content is None or isinstance(content, str))
            for path, content in files.items()
        ):
            raise ValueError("Scoped agent docs output must map paths to text or null")
    except Exception:
        logger.exception("Failed to load scoped agent docs", extra={"ref": ref})
        return {}
    result: dict[str, str] = {}
    for directory in directories:
        for name in _AGENT_DOC_FILENAMES:
            path = posixpath.join(directory, name)
            if path not in files:
                continue
            if files[path] is not None:
                result[path] = files[path]
            break
    return result
