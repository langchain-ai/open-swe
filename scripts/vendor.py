# /// script
# requires-python = ">=3.14"
# ///
"""Vendor an upstream project from its main branch into vendor/.

Each vendored project is one entry of PROJECTS: the Managed Deep Agents SDK (the
`mda` CLI's Rust source and the `managed-deepagents` Python runtime, which mda/
installs and `make mda-cli` builds the CLI from), the default, and langchain-quickjs,
the code mode mda/ runs, from the deepagents repository.

    uv run scripts/vendor.py           # update to the head of upstream main
    uv run scripts/vendor.py <sha>     # update to a commit on upstream main
    uv run scripts/vendor.py --branch <branch> [<sha>]
                                       # a commit on another upstream branch, such as a
                                       # release branch Open SWE needs before it lands
    uv run scripts/vendor.py --pr <n>  # carry upstream pull request <n>, or move it to its head
    uv run scripts/vendor.py --diff    # our changes: the tree against UPSTREAM's commit
    uv run scripts/vendor.py --project langchain-quickjs ...
                                       # any of the above, for another vendored project

UPSTREAM records the exact commit the tree comes from: its full SHA, then its date,
then its branch when that is not main.
Each project's upstream-prs record (vendor/mda-upstream-prs for the SDK) lists the
upstream pull requests the tree carries on top of it, not yet on main: one per line,
its number, the head commit carried, then its title.
Our changes, carried pull requests included, are edits made in the tree itself, as in a
vendor branch. Updating takes upstream's new version of every file we left alone, and
three-way merges upstream's changes into every file we changed (base: the recorded
commit, or where it meets the new one when that doesn't build on it, so a move from main
to a release branch keeps main's newer changes), leaving conflict markers where both
sides changed the same lines; carried pull requests the new commit contains drop out of
that record. Carrying a pull request three-way merges its changes into the tree the same
way (base: where it branched off its base branch, or the head carried before). Both
relock mda/.
Each project's repository variable (MDA_SDK_REPO, DEEPAGENTS_REPO) overrides its
upstream, e.g. a local checkout; pull requests are looked up on GitHub with `gh`.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
VENDOR: Final = ROOT / "vendor"
RECORD: Final = "UPSTREAM"
IGNORED_NAMES: Final = frozenset({"target", "_binaries", "__pycache__"})


class VendorError(Exception):
    """The commit cannot be vendored, or the update left conflicts to resolve."""


def _git(*args: str, cwd: Path, output: bool = False) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=output, text=output)
    return result.stdout.strip() if output else ""


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


@dataclass(frozen=True)
class Project:
    """One upstream project vendored under vendor/: where it comes from and what of it we keep."""

    name: str
    # Overrides ``repo``, e.g. with a local checkout.
    repo_variable: str
    repo: str
    github_repo: str
    # Where in the upstream repository the project lives; its paths are relative to it.
    subdirectory: str
    paths: tuple[str, ...]
    # Upstream paths never vendored, such as the project's own tests.
    skipped: tuple[str, ...] = ()
    # What `make mda-cli` and local tooling write into the tree: kept across updates, never compared.
    build_outputs: tuple[str, ...] = ()
    # Outside the tree, so nothing mistakes it for a file upstream ships.
    prs_record_name: str = ""
    # Where our own changes to the tree are noted, one dated file each.
    changes_name: str = ""

    @property
    def dest(self) -> Path:
        return VENDOR / self.name

    @property
    def prs_record(self) -> Path:
        return VENDOR / (self.prs_record_name or f"{self.name}-upstream-prs")

    @property
    def changes(self) -> str:
        return f"vendor/{self.changes_name or f'{self.name}-changes'}"

    @property
    def upstream_repo(self) -> str:
        return os.environ.get(self.repo_variable, self.repo)

    def skips(self, name: str) -> bool:
        return any(name == skipped or name.startswith(f"{skipped}/") for skipped in self.skipped)

    def recorded_sha(self) -> str:
        return (self.dest / RECORD).read_text().split()[0]

    def tree_ish(self, sha: str) -> str:
        """What ``git archive`` reads the project from at ``sha``."""
        return f"{sha}:{self.subdirectory}" if self.subdirectory else sha


PROJECTS: Final = {
    project.name: project
    for project in (
        Project(
            name="managed-deepagents-sdk",
            repo_variable="MDA_SDK_REPO",
            repo="git@github.com:langchain-ai/managed-deepagents-sdk.git",
            github_repo="langchain-ai/managed-deepagents-sdk",
            subdirectory="",
            paths=(
                "Cargo.toml",
                "Cargo.lock",
                "rustfmt.toml",
                "LICENSE",
                "README.md",
                "crates",
                "packages/pypi",
            ),
            # The Python runtime's own tests never run here.
            skipped=("packages/pypi/tests",),
            build_outputs=("target", "packages/pypi/src/managed_deepagents/_binaries"),
            prs_record_name="mda-upstream-prs",
            changes_name="mda-changes",
        ),
        Project(
            name="langchain-quickjs",
            repo_variable="DEEPAGENTS_REPO",
            repo="git@github.com:langchain-ai/deepagents.git",
            github_repo="langchain-ai/deepagents",
            subdirectory="libs/partners/quickjs",
            paths=("LICENSE", "README.md", "pyproject.toml", "langchain_quickjs"),
        ),
    )
}
DEFAULT_PROJECT: Final = "managed-deepagents-sdk"


@dataclass(frozen=True)
class CarriedPR:
    """An upstream pull request the tree carries, at the head commit carried."""

    number: int
    head: str
    title: str

    def line(self) -> str:
        return f"{self.number} {self.head} {self.title}"

    @classmethod
    def parse(cls, line: str) -> CarriedPR:
        number, head, title = line.split(" ", 2)
        return cls(int(number), head, title)


class CarriedPRs:
    """A project's upstream-prs record: the upstream pull requests the tree carries, by number."""

    def __init__(self, record: Path, prs: dict[int, CarriedPR]) -> None:
        self.record = record
        self.prs = prs

    @classmethod
    def read(cls, project: Project) -> CarriedPRs:
        record = project.prs_record
        lines = record.read_text().splitlines() if record.exists() else []
        return cls(record, {pr.number: pr for pr in map(CarriedPR.parse, filter(None, lines))})

    def without(self, numbers: list[int]) -> CarriedPRs:
        return CarriedPRs(self.record, {n: pr for n, pr in self.prs.items() if n not in numbers})

    def write(self) -> None:
        if self.prs:
            self.record.write_text("".join(f"{self.prs[n].line()}\n" for n in sorted(self.prs)))
        else:
            self.record.unlink(missing_ok=True)


@dataclass(frozen=True)
class PullRequest:
    """An upstream pull request as GitHub has it now."""

    number: int
    title: str
    head: str
    base_ref: str
    merge_commit: str | None

    @classmethod
    def lookup(cls, project: Project, number: int) -> PullRequest:
        fields = "number,title,headRefOid,baseRefName,mergeCommit"
        try:
            found = subprocess.run(
                ["gh", "pr", "view", str(number), "-R", project.github_repo, "--json", fields],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except subprocess.CalledProcessError as exc:
            raise VendorError(
                f"Cannot look up {project.github_repo}#{number}: {exc.stderr.strip()}"
            ) from exc
        data = json.loads(found)
        merge = data["mergeCommit"]
        return cls(
            number=data["number"],
            title=data["title"],
            head=data["headRefOid"],
            base_ref=data["baseRefName"],
            merge_commit=merge["oid"] if merge else None,
        )


class Upstream:
    """One of a project's repository branches, main by default, fetched into a scratch directory."""

    def __init__(self, project: Project, scratch: Path, branch: str = "main") -> None:
        self.project = project
        self.repo = project.upstream_repo
        self.branch = branch
        self._scratch = scratch
        # History without file contents, to check a commit is on the branch; then commits' files.
        self._history = scratch / "history"
        self._content = scratch / "content"
        for path in (self._history, self._content):
            path.mkdir()
            _git("init", "--quiet", cwd=path)
        _git("fetch", "--quiet", "--filter=blob:none", self.repo, branch, cwd=self._history)
        self.main = _git("rev-parse", "FETCH_HEAD", cwd=self._history, output=True)

    def _contains(self, sha: str, commit: str) -> bool:
        """Whether ``commit`` is ``sha`` or one of its ancestors, in the fetched history."""
        known = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=self._history, check=False
        )
        if known.returncode != 0:
            return False
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, commit], cwd=self._history, check=False
        )
        return ancestor.returncode == 0

    def resolve(self, ref: str | None) -> str:
        """The full SHA of ``ref`` (the branch's head by default), which must be on the branch."""
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
                f"No commit {ref} on {self.branch} of {self.repo}; "
                f"give a full SHA from {self.branch}."
            ) from exc
        if not self._contains(sha, self.main):
            raise VendorError(f"{sha} is not on {self.branch} of {self.repo}.")
        return sha

    def merge_base(self, recorded: str, sha: str) -> str:
        """What the tree's changes are against when moving it from ``recorded`` to ``sha``.

        ``recorded`` itself when ``sha`` builds on it. Otherwise, as when moving from main
        to a release branch, where the two meet: what ``recorded`` has and ``sha`` lacks
        then stays, as a merge of the two would keep it.
        """
        if self._contains(recorded, sha):
            return recorded
        _git("fetch", "--quiet", "--filter=blob:none", self.repo, recorded, cwd=self._history)
        return _git("merge-base", recorded, sha, cwd=self._history, output=True)

    def contains(self, pr: PullRequest, commit: str) -> bool:
        """Whether ``commit``, on the branch, already has ``pr``'s changes: it was merged into it."""
        return pr.merge_commit is not None and self._contains(pr.merge_commit, commit)

    def branch_point(self, pr: PullRequest) -> str:
        """Where ``pr`` branched off its base branch: what its changes are against."""
        _git(
            "fetch",
            "--quiet",
            "--filter=blob:none",
            self.repo,
            f"refs/pull/{pr.number}/head",
            f"refs/heads/{pr.base_ref}:refs/base/{pr.number}",
            cwd=self._history,
        )
        return _git("merge-base", pr.head, f"refs/base/{pr.number}", cwd=self._history, output=True)

    def tree(self, sha: str) -> Path:
        """``sha``'s vendored paths, unchanged, with its UPSTREAM record."""
        out = self._scratch / f"tree-{sha}"
        if out.exists():
            return out
        _git("fetch", "--quiet", "--depth", "1", self.repo, sha, cwd=self._content)
        archive = subprocess.run(
            ["git", "archive", self.project.tree_ish(sha), *self.project.paths],
            cwd=self._content,
            check=True,
            capture_output=True,
        ).stdout
        out.mkdir()
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            members = [m for m in tar.getmembers() if not self.project.skips(m.name)]
            tar.extractall(out, members=members, filter="data")
        # The commit itself was fetched here, whichever branch it is on.
        commit = _git("show", "-s", "--format=%H %cs", sha, cwd=self._content, output=True)
        branch = "" if self.branch == "main" else f" {self.branch}"
        (out / RECORD).write_text(f"{commit}{branch}\n")
        return out


def _same(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.read_bytes() == b.read_bytes()


def merge_trees(
    ours: dict[str, Path],
    base: dict[str, Path],
    theirs: dict[str, Path],
    out: Path,
    labels: tuple[str, str, str],
) -> list[str]:
    """Write into ``out`` the three-way merge of ``ours`` and ``theirs``; each path to look at.

    Each conflict says what happened to its path; the file carries conflict markers or the
    version kept.
    """
    conflicts: list[str] = []
    for path in sorted(ours.keys() | base.keys() | theirs.keys()):
        mine, before, after = ours.get(path), base.get(path), theirs.get(path)
        if _same(mine, before) or _same(mine, after):
            kept = after
        elif _same(after, before):
            kept = mine
        elif mine is None:
            kept = after
            conflicts.append(f"{path}: we deleted it, but {labels[2]} changed it")
        elif after is None:
            kept = mine
            conflicts.append(f"{path}: {labels[2]} deleted it; our changed copy is kept")
        else:
            target = out / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(mine, target)
            merged = subprocess.run(
                ["git", "merge-file", "-L", labels[0], "-L", labels[1], "-L", labels[2]]
                + [str(target), str(before or os.devnull), str(after)],
                check=False,
            )
            if merged.returncode < 0:
                raise VendorError(f"git merge-file failed on {path}")
            if merged.returncode > 0:
                conflicts.append(f"{path}: conflict markers where both sides changed it")
            continue
        if kept is not None:
            target = out / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(kept, target)
    return conflicts


def _install(
    project: Project, tree: Path, record: str, prs: CarriedPRs, conflicts: list[str], done: str
) -> str:
    """Replace the vendored tree with ``tree``, recorded as ``record`` carrying ``prs``."""
    dest = project.dest
    (tree / RECORD).write_text(f"{record}\n")
    prs.write()
    # Keep build outputs, so `make mda-cli` need not start over.
    for output in project.build_outputs:
        if (dest / output).exists():
            (tree / output).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(dest / output, tree / output)
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(tree, dest)
    if conflicts:
        raise VendorError(
            f"{done}, but it collides with our changes; settle each of these in "
            f"vendor/{project.name}:\n"
            + "\n".join(f"  {conflict}" for conflict in conflicts)
            + "\nthen run `cd mda && uv lock`."
        )
    subprocess.run(["uv", "lock", "--quiet"], cwd=ROOT / "mda", check=True)
    return done


def update(upstream: Upstream, ref: str | None, scratch: Path) -> str:
    """Move the tree to ``ref``, keeping our changes; what was done."""
    project = upstream.project
    sha = upstream.resolve(ref)
    new = upstream.tree(sha)
    record = (new / RECORD).read_text().strip()
    recorded = project.recorded_sha() if project.dest.exists() else None
    base = upstream.merge_base(recorded, sha) if recorded else None
    tree = scratch / "updated"
    tree.mkdir()
    conflicts = merge_trees(
        files(project.dest),
        files(upstream.tree(base)) if base else {},
        files(new),
        tree,
        ("ours", f"upstream {base[:12]}" if base else "upstream (before)", f"upstream {sha[:12]}"),
    )
    carried = CarriedPRs.read(project)
    merged = [n for n in carried.prs if upstream.contains(PullRequest.lookup(project, n), sha)]
    still = carried.without(merged)
    done = f"Updated vendor/{project.name} to {record}"
    if recorded and base != recorded:
        done += (
            f"; it doesn't build on {recorded[:12]}, so what {recorded[:12]} has and it lacks "
            f"(since {base[:12]}) stays as our change: note it in {project.changes}"
        )
    if merged:
        done += f"; it has #{', #'.join(map(str, sorted(merged)))}, no longer carried"
    if still.prs:
        done += f"; still carrying #{', #'.join(map(str, sorted(still.prs)))}"
    return _install(project, tree, record, still, conflicts, done)


def carry(upstream: Upstream, number: int, scratch: Path) -> str:
    """Merge upstream pull request ``number``'s changes into the tree; what was done."""
    project = upstream.project
    pr = PullRequest.lookup(project, number)
    current = project.recorded_sha()
    if upstream.contains(pr, current):
        raise VendorError(f"#{number} is already in {current}; nothing to carry.")
    carried = CarriedPRs.read(project)
    previous = carried.prs.get(number)
    if previous is not None and previous.head == pr.head:
        raise VendorError(f"#{number} is already carried at its head, {pr.head}.")
    base = upstream.branch_point(pr) if previous is None else previous.head
    tree = scratch / "carried"
    tree.mkdir()
    conflicts = merge_trees(
        files(project.dest),
        files(upstream.tree(base)),
        files(upstream.tree(pr.head)),
        tree,
        ("ours", f"#{number} (before)", f"#{number} {pr.head[:12]}"),
    )
    carried.prs[number] = CarriedPR(number, pr.head, pr.title)
    verb = "Moved" if previous is not None else "Carried"
    done = f"{verb} #{number} to {pr.head}: {pr.title}"
    record = (project.dest / RECORD).read_text().strip()
    return _install(project, tree, record, carried, conflicts, done)


def diff(upstream: Upstream) -> int:
    """Print our changes, the tree against UPSTREAM's commit; how many files differ."""
    project = upstream.project
    # The recorded commit, whichever branch it came from.
    base = upstream.tree(project.recorded_sha())
    theirs, ours = files(base), files(project.dest)
    changed = 0
    for path in sorted(theirs.keys() | ours.keys()):
        before, after = theirs.get(path), ours.get(path)
        if _same(before, after):
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
                f"vendor/{project.name}/{path}",
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
    parser.add_argument(
        "sha", nargs="?", help="a commit on the upstream branch; the branch's head by default"
    )
    parser.add_argument(
        "--project",
        choices=sorted(PROJECTS),
        default=DEFAULT_PROJECT,
        help=f"the vendored project ({DEFAULT_PROJECT} by default)",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="the upstream branch to vendor from (main by default), such as a release branch",
    )
    parser.add_argument(
        "--diff", action="store_true", help="print our changes against UPSTREAM's commit"
    )
    parser.add_argument(
        "--pr", type=int, metavar="N", help="carry upstream pull request N, or move it to its head"
    )
    args = parser.parse_args()
    if sum((args.sha is not None, args.diff, args.pr is not None)) > 1:
        parser.error("give one of a SHA, --pr or --diff")
    project = PROJECTS[args.project]
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)
        try:
            upstream = Upstream(project, scratch, args.branch)
            if args.diff:
                changed = diff(upstream)
                carried = CarriedPRs.read(project).prs
                note = f", carrying #{', #'.join(map(str, sorted(carried)))}" if carried else ""
                print(
                    f"{changed} file(s) differ from {project.recorded_sha()}{note}.",
                    file=sys.stderr,
                )
            elif args.pr is not None:
                print(f"{carry(upstream, args.pr, scratch)}; relocked mda/.")
            else:
                print(f"{update(upstream, args.sha, scratch)}; relocked mda/.")
        except VendorError as exc:
            print(exc, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
