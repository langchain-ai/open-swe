"""Carry a thread's checkout from the sandbox it left to the one it moved to.

The source packs its branch, unpushed commits and uncommitted changes into a git
bundle without touching its working tree or index; the target fetches it and
ends up on the same branch with the same changes uncommitted. Commits already
on ``origin`` stay out of the bundle, so both sides fetch ``origin`` first.
"""

import logging
import re
import shlex
from collections.abc import Mapping

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client

from openswe.bridge.constants import HANDOFF_FROM_KEY
from openswe.sandboxes.connect import connect_sandbox
from openswe.sandboxes.paths import resolve_checkout_dir, resolve_sandbox_work_dir

logger = logging.getLogger(__name__)

_TRANSFER_TIMEOUT_SECONDS = 600
_BRANCH_PREFIX = "branch="

_PACK = """
cd {dir} 2>/dev/null && git rev-parse --git-dir >/dev/null 2>&1 || exit 0
set -e
git fetch -q --prune origin || true
index={bundle}.index
cp "$(git rev-parse --git-path index)" "$index" 2>/dev/null || rm -f "$index"
GIT_INDEX_FILE="$index" git add -A
tree=$(GIT_INDEX_FILE="$index" git write-tree)
rm -f "$index"
wip=$(git -c user.name=open-swe -c user.email=open-swe@users.noreply.github.com \
  commit-tree "$tree" -p HEAD -m "Open SWE handoff")
git update-ref refs/open-swe/handoff "$wip"
git bundle create {bundle} refs/open-swe/handoff --not --remotes=origin 2>/dev/null
git update-ref -d refs/open-swe/handoff
echo "{prefix}$(git symbolic-ref -q --short HEAD || true)"
"""

_UNPACK = """
set -e
test -e {dir}/.git || gh repo clone {repo} {dir}
cd {dir}
git fetch -q origin
git fetch -q {bundle} refs/open-swe/handoff
wip=$(git rev-parse FETCH_HEAD)
b={branch}
if [ -n "$b" ] && ! git show-ref -q --verify "refs/heads/$b"; then
  git checkout -q -f -b "$b" "$wip^"
elif [ -n "$b" ] && git merge-base --is-ancestor "refs/heads/$b" "$wip^" \
  && git checkout -q -f -B "$b" "$wip^" 2>/dev/null; then
  :
else
  git reset -q --hard "$wip^"
fi
git read-tree -u --reset "$wip"
git reset -q
rm -f {bundle}
"""


def _repo(metadata: Mapping[str, object]) -> tuple[str, str] | None:
    repo = metadata.get("repo")
    if not isinstance(repo, Mapping):
        return None
    owner, name = repo.get("owner"), repo.get("name")
    return (owner, name) if isinstance(owner, str) and isinstance(name, str) else None


async def _checkout_dir(backend: SandboxBackendProtocol, name: str) -> str:
    return await resolve_checkout_dir(backend, await resolve_sandbox_work_dir(backend), name)


async def _run(backend: SandboxBackendProtocol, script: str, what: str) -> str:
    result = await backend.aexecute(script, timeout=_TRANSFER_TIMEOUT_SECONDS)
    if result.exit_code not in (0, None):
        raise RuntimeError(f"Could not {what} the thread's checkout: {result.output.strip()}")
    return result.output


async def _pack(source: SandboxBackendProtocol, name: str, bundle: str) -> tuple[str, bytes] | None:
    """The source's branch and a bundle of its work, or ``None`` with no checkout to move."""
    script = _PACK.format(
        dir=shlex.quote(await _checkout_dir(source, name)),
        bundle=shlex.quote(bundle),
        prefix=_BRANCH_PREFIX,
    )
    output = await _run(source, script, "pack")
    branch = re.search(rf"^{_BRANCH_PREFIX}(.*)$", output, re.MULTILINE)
    if branch is None:
        return None
    [download] = await source.adownload_files([bundle])
    await source.aexecute(f"rm -f {shlex.quote(bundle)}")
    if download.content is None:
        raise RuntimeError(f"Could not read the thread's checkout bundle: {download.error}")
    return branch[1], download.content


async def _unpack(
    target: SandboxBackendProtocol, repo: tuple[str, str], bundle: str, branch: str, content: bytes
) -> None:
    [upload] = await target.aupload_files([(bundle, content)])
    if upload.error:
        raise RuntimeError(f"Could not write the thread's checkout bundle: {upload.error}")
    repo_dir = shlex.quote(await _checkout_dir(target, repo[1]))
    script = _UNPACK.format(
        repo=shlex.quote(f"{repo[0]}/{repo[1]}"),
        dir=repo_dir,
        bundle=shlex.quote(bundle),
        branch=shlex.quote(branch),
    )
    await _run(target, script, "unpack")


async def complete_handoff(
    thread_id: str, metadata: Mapping[str, object], target: SandboxBackendProtocol
) -> None:
    """Move the checkout of the sandbox the thread left into ``target``, once."""
    source_id = metadata.get(HANDOFF_FROM_KEY)
    if not isinstance(source_id, str) or source_id == target.id:
        return
    repo = _repo(metadata)
    if repo is not None:
        bundle = f"/tmp/open-swe-handoff-{thread_id}.bundle"
        source = await connect_sandbox(source_id, thread_id=thread_id)
        packed = await _pack(source, repo[1], bundle)
        if packed is not None:
            await _unpack(target, repo, bundle, *packed)
    await get_client().threads.update(thread_id=thread_id, metadata={HANDOFF_FROM_KEY: None})
    logger.info(
        "Handed a thread's checkout over",
        extra={"handoff_thread": thread_id, "handoff_from": source_id, "handoff_to": target.id},
    )
