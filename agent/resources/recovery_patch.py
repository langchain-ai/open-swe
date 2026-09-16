"""Runs inside a thread's sandbox to emit a patch of its uncommitted work.

Delivered as a heredoc by ``agent.threads.diffs``; ``__PAYLOAD__`` is
substituted with a base64 JSON blob before execution. Prints a single JSON line.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

PAYLOAD = json.loads(base64.b64decode("__PAYLOAD__").decode())
WORKSPACE_FALLBACK = Path("/workspace")


def git(repo, args, check=True):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if check and result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or "git " + " ".join(args) + " failed")
    return result


def search_roots():
    roots = [Path.cwd().resolve(), WORKSPACE_FALLBACK]
    seen = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        if root.exists():
            yield root


def repo_names():
    names = PAYLOAD.get("repo_names")
    if not isinstance(names, list):
        return []
    return [name for name in names if isinstance(name, str) and name]


def discovered_paths():
    for root in search_roots():
        yield root
        for child in sorted(root.iterdir()):
            if child.is_dir():
                yield child


def toplevel(path):
    if not (path / ".git").exists():
        return None
    result = git(path, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return None
    root = Path(result.stdout.decode(errors="replace").strip())
    return root if root.exists() else None


def find_repos():
    """Every named repository clone, or the first one discovered when none is named."""
    found, seen = [], set()
    for name in repo_names():
        for root in search_roots():
            repo_root = toplevel(root / Path(name).name)
            if repo_root is not None and repo_root not in seen:
                seen.add(repo_root)
                found.append(repo_root)
                break
    if found:
        return found
    for path in discovered_paths():
        if path in seen:
            continue
        seen.add(path)
        repo_root = toplevel(path)
        if repo_root is not None:
            return [repo_root]
    raise RuntimeError("no git repository found in sandbox workspace")


def safe_ref(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if value.startswith("-") or "\x00" in value or "\n" in value or "\r" in value:
        return None
    return value


def commit_for(repo, ref):
    result = git(repo, ["rev-parse", "--verify", ref + "^{commit}"], check=False)
    if result.returncode == 0:
        return result.stdout.decode(errors="replace").strip()
    return None


def merge_base(repo):
    base_branch = safe_ref(PAYLOAD.get("base_branch")) or "main"
    refs = [
        "origin/" + base_branch,
        base_branch,
        "origin/main",
        "main",
        "origin/master",
        "master",
        "HEAD~1",
    ]
    for ref in refs:
        commit = commit_for(repo, ref)
        if not commit:
            continue
        result = git(repo, ["merge-base", "HEAD", commit], check=False)
        if result.returncode == 0:
            return result.stdout.decode(errors="replace").strip()
        return commit
    return (
        git(repo, ["hash-object", "-t", "tree", "/dev/null"])
        .stdout.decode(errors="replace")
        .strip()
    )


def prefix_args(prefix):
    if not prefix:
        return []
    return ["--src-prefix=a/" + prefix + "/", "--dst-prefix=b/" + prefix + "/"]


def write_repo_patch(patch_file, repo, base, prefix):
    prefixes = prefix_args(prefix)
    tracked = git(repo, ["diff", "--binary", "--full-index", *prefixes, base, "--", "."]).stdout
    if tracked:
        if patch_file.tell() and not tracked.startswith(b"\n"):
            patch_file.write(b"\n")
        patch_file.write(tracked)
    untracked = git(repo, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout
    for raw_path in [p for p in untracked.split(b"\0") if p]:
        rel_path = raw_path.decode("utf-8", errors="surrogateescape")
        full_path = repo / rel_path
        if not full_path.is_file():
            continue
        result = git(
            repo,
            [
                "diff",
                "--no-index",
                "--binary",
                "--full-index",
                *prefixes,
                "--",
                "/dev/null",
                rel_path,
            ],
            check=False,
        )
        if result.returncode not in {0, 1}:
            detail = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(detail or "failed to diff untracked file " + rel_path)
        if result.stdout:
            if patch_file.tell() and not result.stdout.startswith(b"\n"):
                patch_file.write(b"\n")
            patch_file.write(result.stdout)


def write_patch(repos):
    patch_path = Path("/tmp") / ((PAYLOAD.get("thread_key") or "open-swe-recovery") + ".patch")
    with patch_path.open("wb") as patch_file:
        for repo in repos:
            prefix = repo.name if len(repos) > 1 else None
            write_repo_patch(patch_file, repo, merge_base(repo), prefix)
    return patch_path


try:
    patch_path = write_patch(find_repos())
    print(json.dumps({"ok": True, "path": str(patch_path), "size": patch_path.stat().st_size}))
except Exception as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))
    sys.exit(1)
