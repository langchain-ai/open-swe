"""Shared clone-from-cache logic for the sandbox.

The scheduled warm bakes ready-to-use checkouts into a hidden cache dir inside
the sandbox work dir. Taking one is a ``mv`` within a single filesystem -- a
rename, O(1) whatever the repo's size -- so a run gets a full working tree with
real history without paying for a clone. The checkout is only as fresh as the
last warm, so every take fetches origin afterwards.

Full checkouts rather than bare mirrors because materializing a working tree is
the expensive half: hardlinking a bare repo's objects still writes out every
file. It also leaves room to bake installed dependencies into the tree, which
then ride along with the same rename.

A repo that was never baked in still clones, just over the network. Nothing here
depends on the warm -- or on any particular sandbox provider -- having run.
"""

from __future__ import annotations

import posixpath
import shlex

# Hidden and inside the work dir rather than an absolute path like /opt: the
# local provider's work dir is a real directory on the developer's machine, so
# an absolute root would write outside LOCAL_SANDBOX_ROOT_DIR. Dot-prefixed so
# it stays out of `ls` and the agent's view of an otherwise empty work dir.
# Pinned rather than read from the shell's cwd: a snapshot captured from a
# running sandbox carries the filesystem but not the image's WORKDIR, so shells
# there start at `/`. Both the warm and the run must agree on one directory.
PREFERRED_WORK_DIR = "/workspace"

REPO_CACHE_DIRNAME = ".repo-cache"

RESULT_MARKER = "OPENSWE_CLONE"


def _repo_url(owner: str, name: str) -> str:
    return shlex.quote(f"https://github.com/{owner}/{name}.git")


def cache_root_for(work_dir: str) -> str:
    return posixpath.join(work_dir, REPO_CACHE_DIRNAME)


def cache_path_for(work_dir: str, owner: str, name: str) -> str:
    """Path of the baked checkout for ``owner/name``.

    Owner-scoped on purpose: two orgs can both own a repo called ``tools``, and
    a flat layout would silently serve one for the other.
    """
    return posixpath.join(cache_root_for(work_dir), owner, name)


def _resolve_ref_lines(ref: str) -> list[str]:
    """Shell lines that leave the checkout at ``ref``, or the default branch."""
    if ref:
        q_ref = shlex.quote(ref)
        return [
            f"GH_TOKEN=dummy git fetch origin {q_ref} --quiet 2>/dev/null || true",
            f"git checkout --force {q_ref} --quiet",
        ]
    return [
        "GH_TOKEN=dummy git remote set-head origin --auto >/dev/null 2>&1 || true",
        'DEFAULT="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo origin/main)"',
        'DEFAULT="${DEFAULT#origin/}"',
        'git checkout --force -B "$DEFAULT" "origin/$DEFAULT" --quiet',
    ]


def build_clone_script(
    *,
    work_dir: str,
    owner: str,
    name: str,
    ref: str = "",
) -> str:
    """Script that puts ``owner/name`` at ``work_dir/name``, from cache if baked.

    Idempotent: it reuses an existing checkout and refreshes it rather than
    re-cloning over it, so calling it again never clobbers uncommitted work.
    """
    dest = posixpath.join(work_dir, name)
    q_dest = shlex.quote(dest)
    q_work_dir = shlex.quote(work_dir)
    q_cache = shlex.quote(cache_path_for(work_dir, owner, name))
    q_name = shlex.quote(name)

    lines = [
        "set -e",
        f"if [ -d {q_dest}/.git ]; then",
        "  SOURCE=existing",
        f"  cd {q_dest}",
        # A rename, not a copy: both paths live under the work dir, so this is
        # O(1) however large the repo. Falls through to a network clone if the
        # move fails for any reason (a cache on another filesystem, say).
        f"elif [ -d {q_cache} ] && mv {q_cache} {q_dest} 2>/dev/null; then",
        "  SOURCE=cache",
        f"  cd {q_dest}",
        "else",
        "  SOURCE=github",
        f"  mkdir -p {q_work_dir}",
        f"  cd {q_work_dir}",
        f"  GH_TOKEN=dummy git clone {_repo_url(owner, name)} {q_name} --quiet",
        f"  cd {q_dest}",
        "fi",
        # A baked checkout is as stale as the last warm, and an existing one as
        # stale as its last use, so always pull in what came after.
        # A failed fetch is survivable but must not pass for up-to-date: the
        # caller reports it so a day-old tree is never mistaken for current.
        "if GH_TOKEN=dummy git fetch origin --prune --quiet 2>/dev/null; then",
        "  FETCHED=true",
        "else",
        "  FETCHED=false",
        "fi",
        *_resolve_ref_lines(ref),
        f'echo "{RESULT_MARKER} source=$SOURCE fetched=$FETCHED'
        f' path={dest} head=$(git rev-parse HEAD)"',
    ]
    return "\n".join(lines)


def build_mirror_sweep_script(
    work_dir: str,
    repos: list[str],
    *,
    proxy_auth: bool,
) -> str:
    """Script that brings the cache in line with ``repos``, one repo at a time.

    Incremental by design: a repo already in the cache is fetched and reset
    rather than re-cloned, so a warm that starts from yesterday's image only
    pays for the commits since. That matters more the more is baked into each
    checkout -- installed dependencies survive the update instead of being
    thrown away nightly.

    Never aborts on a single repo's failure, and prints one ``ok``/``fail`` line
    per repo so callers can report progress and record what landed.

    Cloning is plain ``git`` over https rather than ``gh repo clone``: ``gh``
    demands an authenticated login even for a public repo, while git over https
    works unauthenticated for public repos and picks up the LangSmith proxy's
    injected credentials for private ones. One command covers both.

    ``proxy_auth`` prefixes ``GH_TOKEN=dummy``, which is how that proxy injects
    real credentials. Providers without it pass ``False`` -- deliberately, so no
    real token is ever interpolated into a command string.

    A failed clone reports git's own error on the ``fail`` line; swallowing it
    leaves an operator with a red status and nothing to diagnose.
    """
    git = "GH_TOKEN=dummy git" if proxy_auth else "git"
    cache_root = cache_root_for(work_dir)
    q_root = shlex.quote(cache_root)
    # Outside the cache root on purpose: anything left inside it is captured
    # into the snapshot, and a stray file breaks hooks that glob
    # `.repo-cache/*/*` for repositories.
    q_err = shlex.quote("/tmp/openswe-warm.err")

    lines = [f"mkdir -p {q_root}"]
    for full_name in repos:
        owner, name = full_name.split("/", 1)
        cache_path = cache_path_for(work_dir, owner, name)
        q_full = shlex.quote(full_name)
        q_cache = shlex.quote(cache_path)
        q_parent = shlex.quote(posixpath.dirname(cache_path))
        lines.extend(
            [
                f"mkdir -p {q_parent}",
                f"if [ -d {q_cache}/.git ] && cd {q_cache} && {_update_checkout(git)}; then",
                f"  echo ok {q_full}",
                "else",
                f"  rm -rf {q_cache}",
                f"  if {git} clone {_repo_url(owner, name)} {q_cache} --quiet 2>{q_err}; then",
                f"    echo ok {q_full}",
                "  else",
                f"    rm -rf {q_cache}",
                f"    echo \"fail {full_name} $(tr '\\n' ' ' < {q_err} | tail -c 200)\"",
                "  fi",
                "fi",
            ]
        )

    lines.extend(_prune_cache_lines(cache_root, repos))
    return "\n".join(lines)


def _update_checkout(git: str) -> str:
    """One `&&` chain that fast-forwards a cached checkout to its default branch.

    Any failure falls through to a fresh clone, so a corrupted or diverged
    checkout self-heals instead of being carried forward for good -- the main
    hazard of building each image from the previous one.
    """
    return (
        f"{git} fetch origin --prune --quiet >/dev/null 2>&1 "
        f"&& {git} remote set-head origin --auto >/dev/null 2>&1 "
        '&& DEFAULT="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)" '
        '&& DEFAULT="${DEFAULT#origin/}" '
        '&& git checkout --force -B "$DEFAULT" "origin/$DEFAULT" --quiet >/dev/null 2>&1 '
        "&& git clean -qfd"
    )


def _prune_cache_lines(cache_root: str, repos: list[str]) -> list[str]:
    """Drop cached repos that are no longer wanted.

    Without this an incremental cache only ever grows: a repo that falls out of
    the ledger would keep its checkout -- and its dependencies -- in every image
    from then on.
    """
    keep = " ".join(sorted(repos))
    q_root = shlex.quote(cache_root)
    return [
        f"KEEP={shlex.quote(keep)}",
        f"for dir in {q_root}/*/*; do",
        '  [ -d "$dir" ] || continue',
        f'  rel="${{dir#{cache_root}/}}"',
        '  case " $KEEP " in',
        '    *" $rel "*) ;;',
        '    *) rm -rf "$dir" ;;',
        "  esac",
        "done",
    ]


def parse_mirror_sweep_output(stdout: str) -> tuple[list[str], list[str]]:
    """Split a sweep's output into ``(cloned, failed)`` repo names."""
    cloned: list[str] = []
    failed: list[str] = []
    buckets = {"ok": cloned, "fail": failed}
    for line in stdout.splitlines():
        status, _, full_name = line.strip().partition(" ")
        bucket = buckets.get(status)
        if bucket is not None and full_name:
            bucket.append(full_name)
    return cloned, failed


def parse_clone_result(stdout: str) -> dict[str, str]:
    """Pull the marker line's fields out of a clone/update script's output."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith(f"{RESULT_MARKER} "):
            continue
        fields: dict[str, str] = {}
        for token in line[len(RESULT_MARKER) + 1 :].split(" "):
            key, sep, value = token.partition("=")
            if sep and key:
                fields[key] = value
        return fields
    return {}
