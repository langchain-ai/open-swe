# /// script
# requires-python = ">=3.14"
# ///
"""Vendor the Managed Deep Agents SDK from its main branch into vendor/managed-deepagents-sdk.

The `mda` CLI's Rust source and the `managed-deepagents` Python runtime, which mda/
installs and `make mda-cli` builds the CLI from.

    uv run scripts/vendor.py           # update to the head of upstream main
    uv run scripts/vendor.py <sha>     # update to a commit on upstream main
    uv run scripts/vendor.py --diff    # our changes: the tree against UPSTREAM's commit

UPSTREAM records the exact commit the tree comes from: its full SHA, then its date.
Our changes are edits made in the tree itself, as in a vendor branch. Updating takes
upstream's new version of every file we left alone, and three-way merges upstream's
changes into every file we changed (base: the recorded commit), leaving conflict
markers where both sides changed the same lines. Updating relocks mda/.
MDA_SDK_REPO overrides the upstream, e.g. a local checkout.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
DEST: Final = ROOT / "vendor" / "managed-deepagents-sdk"
DEFAULT_REPO: Final = "git@github.com:langchain-ai/managed-deepagents-sdk.git"
PATHS: Final = (
    "Cargo.toml",
    "Cargo.lock",
    "rustfmt.toml",
    "LICENSE",
    "README.md",
    "crates",
    "packages/pypi",
)
# The Python runtime's own tests never run here.
SKIPPED: Final = "packages/pypi/tests"
RECORD: Final = "UPSTREAM"
# What `make mda-cli` and local tooling write into the tree: kept across updates, never compared.
BUILD_OUTPUTS: Final = ("target", "packages/pypi/src/managed_deepagents/_binaries")
IGNORED_NAMES: Final = frozenset({"target", "_binaries", "__pycache__"})


class VendorError(Exception):
    """The commit cannot be vendored, or the update left conflicts to resolve."""


def _git(*args: str, cwd: Path, output: bool = False) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=output, text=output)
    return result.stdout.strip() if output else ""


def _skipped(name: str) -> bool:
    return name == SKIPPED or name.startswith(f"{SKIPPED}/")


def _ignored(relative: Path) -> bool:
    # An editable install writes its metadata next to the package.
    return any(part in IGNORED_NAMES or part.endswith(".egg-info") for part in relative.parts)


def files(tree: Path) -> dict[str, Path]:
    """The tree's vendored files by relative path, build outputs and the record aside."""
    if not tree.exists():
        return {}
    found: dict[str, Path] = {}
    for path in tree.rglob("*"):
        relative = path.relative_to(tree)
        if path.is_file() and not _ignored(relative) and relative.as_posix() != RECORD:
            found[relative.as_posix()] = path
    return found


def recorded_sha() -> str:
    return (DEST / RECORD).read_text().split()[0]


class Upstream:
    """The SDK repository's main branch, fetched into a scratch directory."""

    def __init__(self, repo: str, scratch: Path) -> None:
        self.repo = repo
        self._scratch = scratch
        # History without file contents, to check a commit is on main; then commits' files.
        self._history = scratch / "history"
        self._content = scratch / "content"
        for path in (self._history, self._content):
            path.mkdir()
            _git("init", "--quiet", cwd=path)
        _git("fetch", "--quiet", "--filter=blob:none", repo, "main", cwd=self._history)
        self.main = _git("rev-parse", "FETCH_HEAD", cwd=self._history, output=True)

    def resolve(self, ref: str | None) -> str:
        """The full SHA of ``ref`` (main's head by default), which must be on main."""
        if ref is None:
            return self.main
        try:
            sha = _git(
                "rev-parse",
                "--verify",
                "--quiet",
                f"{ref}^{{commit}}",
                cwd=self._history,
                output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise VendorError(
                f"No commit {ref} on main of {self.repo}; give a full SHA from main."
            ) from exc
        on_main = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, self.main], cwd=self._history, check=False
        )
        if on_main.returncode != 0:
            raise VendorError(f"{sha} is not on main of {self.repo}; only main is vendored.")
        return sha

    def tree(self, sha: str) -> Path:
        """``sha``'s vendored paths, unchanged, with its UPSTREAM record."""
        out = self._scratch / f"tree-{sha}"
        if out.exists():
            return out
        _git("fetch", "--quiet", "--depth", "1", self.repo, sha, cwd=self._content)
        archive = subprocess.run(
            ["git", "archive", sha, *PATHS], cwd=self._content, check=True, capture_output=True
        ).stdout
        out.mkdir()
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            members = [m for m in tar.getmembers() if not _skipped(m.name)]
            tar.extractall(out, members=members, filter="data")
        commit = _git("show", "-s", "--format=%H %cs", sha, cwd=self._history, output=True)
        (out / RECORD).write_text(f"{commit}\n")
        return out


def _merge(ours: Path, base: Path, theirs: Path, label: str) -> bool:
    """Three-way merge upstream's change into ``ours``, in place; whether it conflicted."""
    merged = subprocess.run(
        [
            "git",
            "merge-file",
            "-L",
            "ours",
            "-L",
            "upstream (before)",
            "-L",
            label,
            str(ours),
            str(base),
            str(theirs),
        ],
        check=False,
    )
    if merged.returncode < 0:
        raise VendorError(f"git merge-file failed on {ours}")
    return merged.returncode > 0


def update(upstream: Upstream, ref: str | None, scratch: Path) -> str:
    """Move the tree to ``ref``, keeping our changes; the new record."""
    sha = upstream.resolve(ref)
    new = upstream.tree(sha)
    tree = scratch / "updated"
    shutil.copytree(new, tree)
    conflicts: list[str] = []
    notes: list[str] = []
    if DEST.exists():
        old_files = files(upstream.tree(recorded_sha()))
        new_files = files(new)
        ours = files(DEST)
        label = f"upstream {sha[:12]}"
        for path in sorted(old_files.keys() | ours.keys()):
            mine, before, after = ours.get(path), old_files.get(path), new_files.get(path)
            if mine is not None and before is not None and mine.read_bytes() == before.read_bytes():
                continue  # untouched: upstream's new version, or its deletion, stands
            if mine is None and before is not None:
                # We deleted it; keep it deleted unless upstream changed it since.
                if after is not None:
                    (tree / path).unlink()
                    if after.read_bytes() != before.read_bytes():
                        notes.append(f"{path}: we deleted it, and upstream has since changed it")
                continue
            if mine is None:
                continue
            target = tree / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if before is None or after is None:
                # Our addition, or a file we changed that upstream has since deleted: ours.
                shutil.copyfile(mine, target)
                if before is not None:
                    notes.append(f"{path}: upstream deleted it; our changed copy is kept")
                continue
            shutil.copyfile(mine, target)
            if _merge(target, before, after, label):
                conflicts.append(path)
    # Keep build outputs, so `make mda-cli` need not start over.
    for output in BUILD_OUTPUTS:
        if (DEST / output).exists():
            (tree / output).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(DEST / output, tree / output)
    shutil.rmtree(DEST, ignore_errors=True)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(tree, DEST)
    for note in notes:
        print(note)
    if conflicts:
        raise VendorError(
            f"Updated to {sha}, but upstream changed lines we changed too; resolve the conflict "
            "markers in:\n"
            + "\n".join(f"  vendor/managed-deepagents-sdk/{path}" for path in conflicts)
            + "\nthen run `cd mda && uv lock`."
        )
    subprocess.run(["uv", "lock", "--quiet"], cwd=ROOT / "mda", check=True)
    return (DEST / RECORD).read_text().strip()


def diff(upstream: Upstream) -> int:
    """Print our changes, the tree against UPSTREAM's commit; how many files differ."""
    base = upstream.tree(upstream.resolve(recorded_sha()))
    theirs, ours = files(base), files(DEST)
    changed = 0
    for path in sorted(theirs.keys() | ours.keys()):
        before, after = theirs.get(path), ours.get(path)
        if before is not None and after is not None and before.read_bytes() == after.read_bytes():
            continue
        changed += 1
        sys.stdout.flush()
        subprocess.run(
            [
                "diff",
                "-u",
                "--label",
                f"upstream/{path}",
                "--label",
                f"vendor/managed-deepagents-sdk/{path}",
                str(before or os.devnull),
                str(after or os.devnull),
            ],
            check=False,
        )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("sha", nargs="?", help="a commit on upstream main; main's head by default")
    parser.add_argument(
        "--diff", action="store_true", help="print our changes against UPSTREAM's commit"
    )
    args = parser.parse_args()
    if args.sha is not None and args.diff:
        parser.error("--diff compares against the commit UPSTREAM records; give no SHA")
    repo = os.environ.get("MDA_SDK_REPO", DEFAULT_REPO)
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)
        try:
            upstream = Upstream(repo, scratch)
            if args.diff:
                changed = diff(upstream)
                print(f"{changed} file(s) differ from {recorded_sha()}.", file=sys.stderr)
            else:
                commit = update(upstream, args.sha, scratch)
                print(
                    f"Vendored {commit} from main of {repo} into vendor/managed-deepagents-sdk, "
                    "keeping our changes, and relocked mda/."
                )
        except VendorError as exc:
            print(exc, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
