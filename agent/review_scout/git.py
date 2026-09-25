"""Git plumbing for the review scout, run inside its sandbox checkout.

Setup leaves the PR's full diff as unstaged changes on top of the merge base.
The scout commits it in reading order; :func:`finalize` then guarantees the
last commit's tree is exactly the PR head's tree and attributes every changed
line back to the commit that introduced (or removed) it, in the numbering the
PR's own diff uses.
"""

import re
import shlex
from dataclasses import dataclass, field

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.review.walkthrough import FileLines, LineRange, StepDraft

GIT_TIMEOUT_SECONDS = 300
SCOUT_KIND_TRAILER = "Scout-Kind"
OTHER_TITLE = "Other changes"
LEFTOVER_SUMMARY = "Changes the walkthrough did not place in a step."

_IDENTITY = "-c user.name='Open SWE Review Scout' -c user.email=review-scout@open-swe.invalid"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_BLAME_GROUP = "^[0-9a-f]{40} [0-9]+ [0-9]+ [0-9]+$"
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# Owner for changed lines no commit claims; they join the "Other" step.
_UNOWNED = -1
_RECORD = "\x1e"
_UNIT = "\x1f"


class ScoutGitError(RuntimeError):
    """A git step in the scout sandbox failed."""


@dataclass
class _Commit:
    sha: str
    title: str
    summary: str
    is_other: bool
    touched: list[str] = field(default_factory=list)


@dataclass
class _ChangedFile:
    old_path: str | None
    new_path: str | None


async def _run(backend: SandboxBackendProtocol, repo_dir: str, script: str) -> str:
    result = await backend.aexecute(
        f"set -e\ncd {shlex.quote(repo_dir)}\n{script}", timeout=GIT_TIMEOUT_SECONDS
    )
    if result.exit_code not in (0, None):
        raise ScoutGitError(result.output.strip()[-2000:])
    return result.output


def _require_sha(value: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise ScoutGitError("expected a full commit SHA")
    return value


async def setup_working_tree(
    backend: SandboxBackendProtocol, repo_dir: str, *, base_sha: str, head_sha: str
) -> str:
    """Leave head's tree unstaged on top of the merge base; returns the merge base."""
    base, head = _require_sha(base_sha), _require_sha(head_sha)
    output = await _run(
        backend,
        repo_dir,
        f"git reset --quiet --hard {head}\n"
        "git clean -fdq\n"
        f"mb=$(git merge-base {base} {head})\n"
        'git reset --quiet --mixed "$mb"\n'
        # Intent-to-add puts new files in `git diff` without staging them.
        "git ls-files -z --others --exclude-standard"
        " | xargs -0 git add --intent-to-add -- 2>/dev/null || true\n"
        'echo "$mb"',
    )
    return _require_sha(output.strip().splitlines()[-1].strip())


async def committed_kinds(
    backend: SandboxBackendProtocol, repo_dir: str, *, base_sha: str, head_sha: str
) -> list[bool]:
    """Whether each commit the scout has made so far is an "other" commit, oldest first."""
    base, head = _require_sha(base_sha), _require_sha(head_sha)
    fmt = shlex.quote(f"%(trailers:key={SCOUT_KIND_TRAILER},valueonly){_RECORD}")
    log = await _run(
        backend,
        repo_dir,
        f"mb=$(git merge-base {base} {head})\n"
        f'git log --first-parent --reverse --format={fmt} "$mb"..HEAD',
    )
    return [kind.strip() == "other" for kind in log.split(_RECORD)[:-1]]


async def commit_staged(
    backend: SandboxBackendProtocol, repo_dir: str, *, title: str, summary: str, other: bool
) -> str | None:
    """Commit the index as one walkthrough step; ``None`` when nothing is staged.

    An "other" commit may be empty, so a pull request with nothing mechanical can still open with one.
    """
    kind = "other" if other else "step"
    message = ["-m", title]
    if summary:
        message += ["-m", summary]
    quoted = " ".join(shlex.quote(part) for part in message)
    trailer = shlex.quote(f"{SCOUT_KIND_TRAILER}: {kind}")
    empty_check = (
        "" if other else "if git diff --cached --quiet; then echo NOTHING_STAGED; exit 0; fi\n"
    )
    allow_empty = " --allow-empty" if other else ""
    output = await _run(
        backend,
        repo_dir,
        f"{empty_check}"
        f"git {_IDENTITY} commit --quiet --no-verify{allow_empty} {quoted} --trailer {trailer}\n"
        "git rev-parse HEAD",
    )
    last = output.strip().splitlines()[-1].strip() if output.strip() else ""
    return None if last == "NOTHING_STAGED" else last


async def finalize(
    backend: SandboxBackendProtocol, repo_dir: str, *, merge_base: str, head_sha: str
) -> list[StepDraft]:
    """Close the walkthrough on head's exact tree and attribute every changed line."""
    mb, head = _require_sha(merge_base), _require_sha(head_sha)
    leftover_trailer = shlex.quote(f"{SCOUT_KIND_TRAILER}: other")
    await _run(
        backend,
        repo_dir,
        "last=$(git rev-parse HEAD)\n"
        f'if ! git diff --quiet "$last" {head}; then\n'
        f"  tree=$(git rev-parse {head}^{{tree}})\n"
        f'  next=$(git {_IDENTITY} commit-tree "$tree" -p "$last" '
        f"-m {shlex.quote(OTHER_TITLE)} -m {shlex.quote(LEFTOVER_SUMMARY)} "
        f"-m {leftover_trailer})\n"
        '  git reset --quiet --soft "$next"\n'
        "fi",
    )
    commits = await _commits(backend, repo_dir, mb)
    if not commits:
        return []
    files = await _changed_files(backend, repo_dir, mb)
    claimed_added, claimed_deleted = await _blame(backend, repo_dir, mb, commits, files)
    pr_added, pr_deleted = await _pr_changed_lines(backend, repo_dir, mb, files)
    return _steps(
        commits,
        files,
        _reconcile(claimed_added, pr_added),
        _reconcile(claimed_deleted, pr_deleted),
    )


async def _commits(backend: SandboxBackendProtocol, repo_dir: str, mb: str) -> list[_Commit]:
    fmt = f"{_RECORD}%H{_UNIT}%s{_UNIT}%b{_UNIT}%(trailers:key={SCOUT_KIND_TRAILER},valueonly)"
    log = await _run(
        backend,
        repo_dir,
        f"git log --first-parent --reverse --format={shlex.quote(fmt)} {mb}..HEAD",
    )
    commits: list[_Commit] = []
    for record in log.split(_RECORD)[1:]:
        sha, title, body, kind = (record.split(_UNIT) + ["", "", ""])[:4]
        summary = "\n".join(
            line for line in body.splitlines() if not line.startswith(f"{SCOUT_KIND_TRAILER}:")
        ).strip()
        commits.append(
            _Commit(
                sha=_require_sha(sha.strip()),
                title=title.strip(),
                summary=summary,
                is_other=kind.strip() == "other",
            )
        )
    touched = await _run(
        backend,
        repo_dir,
        "for c in " + " ".join(c.sha for c in commits) + f'; do printf "{_RECORD}%s\\n" "$c"; '
        'git diff-tree -r -z --no-commit-id --name-only --no-renames "$c"; done',
    )
    by_sha = {c.sha: c for c in commits}
    for record in touched.split(_RECORD)[1:]:
        sha, _, paths = record.partition("\n")
        commit = by_sha.get(sha.strip())
        if commit is not None:
            commit.touched = [p for p in paths.split("\0") if p]
    return commits


async def _changed_files(
    backend: SandboxBackendProtocol, repo_dir: str, mb: str
) -> list[_ChangedFile]:
    raw = await _run(backend, repo_dir, f"git diff --name-status -z -M {mb} HEAD")
    parts = [p for p in raw.split("\0") if p]
    files: list[_ChangedFile] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if status[:1] in ("R", "C") and i + 2 < len(parts):
            files.append(_ChangedFile(old_path=parts[i + 1], new_path=parts[i + 2]))
            i += 3
            continue
        path = parts[i + 1] if i + 1 < len(parts) else ""
        if status == "A":
            files.append(_ChangedFile(old_path=None, new_path=path))
        elif status == "D":
            files.append(_ChangedFile(old_path=path, new_path=None))
        else:
            files.append(_ChangedFile(old_path=path, new_path=path))
        i += 2
    return files


_Attribution = dict[str, dict[int, list[int]]]


async def _blame(
    backend: SandboxBackendProtocol,
    repo_dir: str,
    mb: str,
    commits: list[_Commit],
    files: list[_ChangedFile],
) -> tuple[_Attribution, _Attribution]:
    """Per path, per commit index: head lines it added and merge-base lines it deleted.

    Forward blame over ``mb..HEAD`` names the commit that introduced each head
    line. Reverse blame names the last commit each merge-base line survived
    in, so the commit after it is the one that removed it.
    """
    grep = f"grep -E {shlex.quote(_BLAME_GROUP)} | cut -d' ' -f1,3,4"
    lines: list[str] = []
    for index, file in enumerate(files):
        if file.new_path is not None:
            lines.append(f'printf "{_RECORD}A{index}\\n"')
            lines.append(
                f"git blame --incremental {mb}..HEAD -- {shlex.quote(file.new_path)} | {grep} || true"
            )
        if file.old_path is not None:
            lines.append(f'printf "{_RECORD}D{index}\\n"')
            lines.append(
                f"git blame --incremental --reverse {mb}..HEAD -- {shlex.quote(file.old_path)}"
                f" | {grep} || true"
            )
    output = await _run(backend, repo_dir, "\n".join(lines)) if lines else ""
    position = {c.sha: i for i, c in enumerate(commits)}
    last = len(commits) - 1
    added: _Attribution = {}
    deleted: _Attribution = {}
    for record in output.split(_RECORD)[1:]:
        header, _, body = record.partition("\n")
        side, index = header[:1], int(header[1:])
        file = files[index]
        for row in body.splitlines():
            sha, _, rest = row.partition(" ")
            start_text, _, count_text = rest.partition(" ")
            if not (start_text.isdigit() and count_text.isdigit()):
                continue
            start, count = int(start_text), int(count_text)
            if side == "A":
                owner = position.get(sha)
                if owner is None or file.new_path is None:
                    continue
                target = added.setdefault(file.new_path, {})
            else:
                survived_in = position.get(sha, -1)
                if survived_in == last or file.old_path is None:
                    continue
                owner = survived_in + 1
                target = deleted.setdefault(file.old_path, {})
            target.setdefault(owner, []).extend(range(start, start + count))
    return added, deleted


async def _pr_changed_lines(
    backend: SandboxBackendProtocol, repo_dir: str, mb: str, files: list[_ChangedFile]
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """The PR diff's own changed lines: added head lines by new path, deleted merge-base lines by old path.

    Diffed one file at a time with the paths from ``--name-status -z``, so a
    path git would quote or tab-terminate in a patch header is never parsed.
    """
    lines: list[str] = []
    for index, file in enumerate(files):
        paths = " ".join(shlex.quote(p) for p in {file.old_path, file.new_path} if p)
        lines.append(f'printf "{_RECORD}{index}\\n"')
        lines.append(f"git diff -U0 --no-color --no-ext-diff -M {mb} HEAD -- {paths}")
    raw = await _run(backend, repo_dir, "\n".join(lines)) if lines else ""
    added: dict[str, set[int]] = {}
    deleted: dict[str, set[int]] = {}
    for record in raw.split(_RECORD)[1:]:
        header, _, body = record.partition("\n")
        file = files[int(header)]
        for line in body.splitlines():
            match = _HUNK_RE.match(line)
            if match is None:
                continue
            old_start, old_count, new_start, new_count = (
                int(group) if group is not None else 1 for group in match.groups()
            )
            if file.old_path is not None:
                deleted.setdefault(file.old_path, set()).update(
                    range(old_start, old_start + old_count)
                )
            if file.new_path is not None:
                added.setdefault(file.new_path, set()).update(
                    range(new_start, new_start + new_count)
                )
    return added, deleted


def _reconcile(claimed: _Attribution, changed: dict[str, set[int]]) -> _Attribution:
    """Make the PR diff decide which lines changed and blame decide who owns them.

    Blame over the intermediate commits can align repeated text differently
    from the PR diff, so it may claim an unchanged line and miss the changed
    twin next to it. Those misses are handed the owners of the spurious claims
    in order, and anything still unowned goes to the "Other" step.
    """
    result: _Attribution = {}
    for path in claimed.keys() | changed.keys():
        lines = changed.get(path, set())
        owner_of = {n: owner for owner, numbers in claimed.get(path, {}).items() for n in numbers}
        owners = {n: owner for n, owner in owner_of.items() if n in lines}
        spurious = [owner_of[n] for n in sorted(owner_of) if n not in lines]
        orphans = sorted(n for n in lines if n not in owner_of)
        for index, n in enumerate(orphans):
            owners[n] = spurious[index] if index < len(spurious) else _UNOWNED
        per_owner: dict[int, list[int]] = {}
        for n, owner in owners.items():
            per_owner.setdefault(owner, []).append(n)
        if per_owner:
            result[path] = per_owner
    return result


def _ranges(numbers: list[int]) -> list[LineRange]:
    ranges: list[LineRange] = []
    for n in sorted(set(numbers)):
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges


def _steps(
    commits: list[_Commit],
    files: list[_ChangedFile],
    added: _Attribution,
    deleted: _Attribution,
) -> list[StepDraft]:
    """Group commits into steps, folding every "other" commit into one final step."""
    steps: list[StepDraft] = []
    other: StepDraft | None = None
    other_commits: list[int] = []
    step_of: dict[int, int] = {}
    for i, commit in enumerate(commits):
        if commit.is_other:
            if other is None:
                other = StepDraft(title=OTHER_TITLE, summary=commit.summary, is_other=True)
            other_commits.append(i)
        else:
            step_of[i] = len(steps)
            steps.append(StepDraft(title=commit.title, summary=commit.summary))
    has_unowned = any(_UNOWNED in owners for owners in (*added.values(), *deleted.values()))
    if other is None and has_unowned:
        other = StepDraft(title=OTHER_TITLE, summary=LEFTOVER_SUMMARY, is_other=True)
    if other is not None:
        steps.append(other)
        for i in (*other_commits, _UNOWNED):
            step_of[i] = len(steps) - 1

    per_step: dict[int, dict[str, FileLines]] = {}

    def file_lines(step: int, path: str) -> FileLines:
        return per_step.setdefault(step, {}).setdefault(path, FileLines(path=path))

    display_path = {f.old_path: f.new_path or f.old_path for f in files if f.old_path}
    attributed: set[str] = set()
    for path, owners in added.items():
        for commit_index, numbers in owners.items():
            file_lines(step_of[commit_index], path).added.extend(_ranges(numbers))
            attributed.add(path)
    for old_path, owners in deleted.items():
        path = display_path.get(old_path) or old_path
        for commit_index, numbers in owners.items():
            if commit_index in step_of:
                file_lines(step_of[commit_index], path).deleted.extend(_ranges(numbers))
                attributed.add(path)
    # Binary files, renames and mode changes carry no lines: they belong to the
    # first commit that touched them.
    changed = {f.new_path or f.old_path for f in files}
    for i, commit in enumerate(commits):
        for path in commit.touched:
            shown = display_path.get(path, path)
            if shown in changed and shown not in attributed:
                file_lines(step_of[i], shown)
                attributed.add(shown)

    result: list[StepDraft] = []
    for index, step in enumerate(steps):
        owned = per_step.get(index)
        if not owned:
            continue
        step.files = sorted(owned.values(), key=lambda f: f.path)
        result.append(step)
    return result
