# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic>=2.12"]
# ///
"""Assemble the preview branch from main, preview-manual and labelled PRs, or reset it."""

import asyncio
import hashlib
import os
import re
import shutil
import signal
import sys
import tempfile
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, TypeAdapter

CONFLICT_LIMIT = 10
PROMPT_PATH = Path(".github/prompts/resolve_preview_conflict.md")
FIX_PROMPT_PATH = Path(".github/prompts/fix_preview_typecheck.md")
FIXUP_MESSAGE = "preview: fix typecheck errors (oswe)"
FORCE_MESSAGE = "preview: force deployment"
TYPECHECK_IMAGE = "node:24-bookworm-slim"
TYPECHECK_SCRIPT = (
    "corepack pnpm install --frozen-lockfile --ignore-scripts --ignore-pnpmfile"
    " && corepack pnpm --filter open-swe-dashboard run typecheck"
)
TYPECHECK_OUTPUT_LIMIT = 20_000
FAILED_REF = "refs/preview-failed"
PUBLISHED_REF = "refs/preview-published"
INPUTS_REF = "refs/preview-inputs/latest"
TYPECHECK_DIAGNOSTIC = re.compile(r"(?m)^([^\n(]+?)(?:\(\d+,\d+\):|:\d+:\d+\s+-)\s*error TS\d+:")
MERGED_LINE = re.compile(r"merged:((?: \d+)*)")
LEFT_OUT_LINE = re.compile(r"#(\d+): (.+)")
AGENT_INTERRUPT_GRACE_SECONDS = 60
# oswe needs the OIDC request pair to authenticate and strips it from the agent's shell itself.
AGENT_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "OPEN_SWE_BACKEND_URL",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    }
)
RERERE_NOTE = "conflicts resolved from the rerere cache"
AGENT_NOTE = "conflicts resolved by oswe"
RESET_REF_PREFIX = "refs/preview-reset/"
RERERE_REF = "refs/preview-rerere/cache"
RERERE_DIR = Path(".git/rr-cache")


class PreviewError(Exception):
    """A step the preview build depends on failed."""


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Settings:
    repo: str
    branch: str
    label: str
    manual_branch: str
    max_prs: int
    reset_hour: int
    reset_zone: ZoneInfo
    url: str
    agent_timeout_seconds: float
    force: bool

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            repo=os.environ["GH_REPO"],
            branch=_env("PREVIEW_BRANCH", "preview"),
            label=_env("PREVIEW_LABEL", "preview"),
            manual_branch=_env("PREVIEW_MANUAL_BRANCH", "preview-manual"),
            max_prs=int(_env("PREVIEW_MAX_PRS", "50")),
            reset_hour=int(_env("PREVIEW_RESET_HOUR", "7")),
            reset_zone=ZoneInfo(_env("PREVIEW_RESET_ZONE", "America/New_York")),
            url=_env(
                "PREVIEW_URL",
                "https://open-swe-preview-cc53e8fbe667565d843d0843f84ee92c.us.langgraph.app/agents",
            ),
            agent_timeout_seconds=float(_env("PREVIEW_AGENT_TIMEOUT_SECONDS", "1800")),
            force=os.environ.get("FORCE") == "true",
        )


class Label(BaseModel):
    name: str


class Repository(BaseModel):
    full_name: str


class Head(BaseModel):
    sha: str
    repo: Repository | None = None


class Author(BaseModel):
    login: str


class Pull(BaseModel):
    number: int
    title: str
    html_url: str
    head: Head
    user: Author
    labels: list[Label]

    def has_label(self, name: str) -> bool:
        return any(label.name == name for label in self.labels)

    @property
    def link(self) -> str:
        return f"[#{self.number} {self.title}]({self.html_url}) — @{self.user.login}"

    @property
    def merge_message(self) -> str:
        return f"preview: merge PR #{self.number} from @{self.user.login}"


class Comment(BaseModel):
    body: str


PULL_PAGES = TypeAdapter(list[list[Pull]])
COMMENT_PAGES = TypeAdapter(list[list[Comment]])


@dataclass(frozen=True)
class BuildInputs:
    main_sha: str
    pulls: tuple[Pull, ...]
    manual_sha: str | None

    @property
    def fingerprint(self) -> str:
        pulls = "".join(
            f"{pull.number}\0{pull.head.sha}\0"
            for pull in sorted(self.pulls, key=lambda pull: pull.number)
        )
        return hashlib.sha256(
            f"{self.main_sha}\0{pulls}manual\0{self.manual_sha or 'absent'}".encode()
        ).hexdigest()

    @classmethod
    async def load(cls, settings: Settings) -> Self:
        pulls = tuple(
            pull for pull in await open_pulls(settings.repo) if pull.has_label(settings.label)
        )
        exists = await remote_branch_exists(settings.manual_branch)
        if exists is None:
            raise PreviewError(f"could not look up {settings.manual_branch}")
        manual_sha = None
        if exists:
            ref = "refs/preview-manual"
            await git(
                "fetch",
                "--no-tags",
                "--force",
                "origin",
                f"refs/heads/{settings.manual_branch}:{ref}",
            )
            manual_sha = await rev_parse(ref)
        return cls(await rev_parse("origin/main"), pulls, manual_sha)


@dataclass(frozen=True)
class Completed:
    code: int
    stdout: str
    stderr: str

    @property
    def first_line(self) -> str:
        text = self.stderr.strip() or self.stdout.strip()
        return text.splitlines()[0] if text else ""


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr, flush=True)


async def run(*args: str, check: bool = True, env: dict[str, str] | None = None) -> Completed:
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, env=env
    )
    stdout, stderr = await proc.communicate()
    result = Completed(await proc.wait(), stdout.decode(), stderr.decode())
    if check and result.code != 0:
        raise PreviewError(f"{' '.join(args)} exited {result.code}: {result.stderr.strip()}")
    return result


async def git(*args: str, check: bool = True, env: dict[str, str] | None = None) -> Completed:
    return await run("git", *args, check=check, env=env)


async def gh_api(path: str, *args: str, check: bool = True) -> Completed:
    return await run("gh", "api", *args, path, check=check)


async def rev_parse(ref: str) -> str:
    return (await git("rev-parse", ref)).stdout.strip()


async def remote_refs(pattern: str) -> list[str]:
    listing = (await git("ls-remote", "origin", pattern)).stdout
    return [line.split("\t", 1)[1] for line in listing.splitlines() if "\t" in line]


async def remote_branch_exists(branch: str) -> bool | None:
    """Whether ``branch`` exists on origin, or None when the lookup itself failed."""
    lookup = await git(
        "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}", check=False
    )
    match lookup.code:
        case 0:
            return True
        case 2:
            return False
        case _:
            return None


async def fetch_main() -> None:
    await git("fetch", "--no-tags", "origin", "main")


async def open_pulls(repo: str) -> list[Pull]:
    listing = await gh_api(f"repos/{repo}/pulls?state=open&per_page=100", "--paginate", "--slurp")
    return [pull for page in PULL_PAGES.validate_json(listing.stdout) for pull in page]


async def discard_uncommitted() -> None:
    await git("reset", "-q", "--hard")
    await git("clean", "-fdq")


async def restore_head(commit: str) -> None:
    await git("rerere", "clear")
    await git("reset", "-q", "--hard", commit)
    await git("clean", "-fdq")


@dataclass
class RerereCache:
    """``.git/rr-cache`` kept as a tree in a ref: Actions caches are read-only on PR events."""

    restored_tree: str | None = None

    @staticmethod
    def _index_env(index: Path) -> dict[str, str]:
        return {**os.environ, "GIT_INDEX_FILE": str(index)}

    async def restore(self) -> None:
        if not await remote_refs(RERERE_REF):
            return
        await git("fetch", "--no-tags", "--force", "origin", f"{RERERE_REF}:{RERERE_REF}")
        self.restored_tree = await rev_parse(f"{RERERE_REF}^{{tree}}")
        with tempfile.TemporaryDirectory() as scratch:
            env = self._index_env(Path(scratch) / "index")
            await git("read-tree", RERERE_REF, env=env)
            await git("checkout-index", "-a", "-f", f"--prefix={RERERE_DIR}/", env=env)

    def discard(self) -> None:
        if RERERE_DIR.is_dir():
            shutil.rmtree(RERERE_DIR)
        self.restored_tree = None

    async def save(self) -> None:
        if not RERERE_DIR.is_dir():
            return
        with tempfile.TemporaryDirectory() as scratch:
            env = self._index_env(Path(scratch) / "index")
            await git("--work-tree", str(RERERE_DIR), "add", "-A", ".", env=env)
            tree = (await git("write-tree", env=env)).stdout.strip()
        if tree == self.restored_tree:
            return
        commit = (await git("commit-tree", tree, "-m", "preview: rerere cache")).stdout.strip()
        pushed = await git("push", "--force", "origin", f"{commit}:{RERERE_REF}", check=False)
        if pushed.code != 0:
            warn(f"could not save the rerere cache: {pushed.first_line}")


@dataclass(frozen=True)
class Merged:
    note: str | None = None
    paths: tuple[str, ...] = ()

    def suffix(self) -> str:
        return f" — {self.note}" if self.note else ""


@dataclass(frozen=True)
class Conflicted:
    paths: tuple[str, ...]
    agent_note: str | None = None


@dataclass(frozen=True)
class Unmergeable:
    reason: str


type MergeOutcome = Merged | Conflicted | Unmergeable


async def merge(sha: str, message: str) -> MergeOutcome:
    before = await rev_parse("HEAD")
    result = await git("merge", "--no-ff", "-m", message, sha, check=False)
    if result.code == 0:
        return Merged()
    unmerged = (await git("diff", "--name-only", "--diff-filter=U", "-z")).stdout
    conflicts = tuple(path for path in unmerged.split("\0") if path)
    resolved = (await git("ls-files", "--resolve-undo", "-z")).stdout
    paths = tuple(
        sorted(
            set(conflicts)
            | {entry.split("\t", 1)[1] for entry in resolved.split("\0") if "\t" in entry}
        )
    )
    merging = (await git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)).code == 0
    if (
        merging
        and not conflicts
        and (await git("commit", "-q", "--no-edit", check=False)).code == 0
    ):
        await discard_uncommitted()
        return Merged(RERERE_NOTE, paths)
    if conflicts:
        await restore_head(before)
        return Conflicted(paths)
    unrelated = (await git("merge-base", "HEAD", sha, check=False)).code != 0
    await git("merge", "--abort", check=False)
    if unrelated:
        return Unmergeable(f"could not be merged — {result.first_line}")
    raise PreviewError(f"merge {sha[:7]} failed: {result.stderr or result.stdout}")


@dataclass(frozen=True)
class Pending:
    pull: Pull
    sha: str
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class AgentReport:
    merged: frozenset[int]
    reasons: dict[int, str]

    @classmethod
    def parse(cls, stdout: str) -> Self | None:
        """The report in the prompt's format, or None when stdout does not follow it."""
        lines = stdout.strip().splitlines()
        if not lines or not (head := MERGED_LINE.fullmatch(lines[0])):
            return None
        reasons: dict[int, str] = {}
        for line in lines[1:]:
            if not (left_out := LEFT_OUT_LINE.fullmatch(line)):
                return None
            reasons[int(left_out.group(1))] = left_out.group(2)
        return cls(frozenset(int(number) for number in head.group(1).split()), reasons)


async def run_agent(prompt: str, stdin: str, timeout: float) -> Completed:
    env = {name: value for name, value in os.environ.items() if name in AGENT_ENV}
    proc = await asyncio.create_subprocess_exec(
        "oswe", "run", prompt, stdin=PIPE, stdout=PIPE, env=env
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(stdin.encode()), timeout)
    except TimeoutError:
        warn(f"oswe ran past {timeout:.0f}s; interrupting it")
        proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), AGENT_INTERRUPT_GRACE_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        stdout = b""
    report = stdout.decode()
    print(report, file=sys.stderr, flush=True)
    await discard_uncommitted()
    return Completed(await proc.wait(), report, "")


async def resolve_with_agent(
    prompt: str, pending: list[Pending], timeout: float
) -> AgentReport | None:
    """Hand every conflicting PR to one oswe run and return what it reports doing."""
    before = await rev_parse("HEAD")
    listing = "\n".join(f"#{item.pull.number} {item.sha} {item.pull.title}" for item in pending)
    print(f"merging {len(pending)} conflicting PR(s) with oswe", file=sys.stderr, flush=True)
    result = await run_agent(prompt, listing, timeout)
    if parsed := AgentReport.parse(result.stdout):
        return parsed
    warn(f"oswe exited {result.code} without a report in the required format; discarding its work")
    await restore_head(before)
    return None


async def typecheck() -> str | None:
    """Errors from typechecking the dashboard at HEAD, or None when it is clean.

    The Docker build installs and bundles the UI the same way but swallows failures, so this
    is the only place a broken UI stops the preview instead of shipping without a dashboard.
    The check runs the PRs' own toolchain, so it gets an exported copy of the tree in a
    container: no ``.git`` credentials, no runner environment, no view of this process.
    """
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "tree.tar"
        source = Path(scratch) / "src"
        source.mkdir()
        await git("archive", "--format=tar", "-o", str(archive), "HEAD")
        await run("tar", "-xf", str(archive), "-C", str(source))
        result = await run(
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/tmp",
            "-e",
            "COREPACK_HOME=/tmp/corepack",
            "-e",
            "COREPACK_ENABLE_DOWNLOAD_PROMPT=0",
            "-v",
            f"{source}:/src",
            "-w",
            "/src",
            TYPECHECK_IMAGE,
            "sh",
            "-c",
            TYPECHECK_SCRIPT,
            check=False,
        )
    if result.code == 0:
        return None
    return f"$ {TYPECHECK_SCRIPT}\n{result.stdout}{result.stderr}"[-TYPECHECK_OUTPUT_LIMIT:]


async def fix_with_agent(prompt: str, errors: str, timeout: float) -> None:
    """Let oswe commit a fix for ``errors`` on HEAD, squashed into one fix-up commit."""
    before = await rev_parse("HEAD")
    print("fixing the preview typecheck with oswe", file=sys.stderr, flush=True)
    await run_agent(prompt, errors, timeout)
    if await rev_parse("HEAD^{tree}") == await rev_parse(f"{before}^{{tree}}"):
        await restore_head(before)
        return
    await git("reset", "-q", "--soft", before)
    await git("commit", "-q", "-m", FIXUP_MESSAGE)


def conflict_marker(sha: str, paths: tuple[str, ...]) -> str:
    listed = "".join(f"{path}\0" for path in sorted(paths)) if paths else "\0"
    digest = hashlib.sha256(f"{sha}\0{listed}".encode()).hexdigest()
    return f"<!-- preview-conflict:{digest[:16]} -->"


def summary(*lines: str) -> None:
    text = "\n".join(lines) + "\n"
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        sys.stdout.write(text)
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        warn(f"could not write the summary: {exc}")


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def listed_paths(paths: tuple[str, ...]) -> str:
    return "".join(f"\n  - `{path}`" for path in paths[:CONFLICT_LIMIT])


@dataclass
class Preview:
    settings: Settings
    included: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    conflicted: bool = False
    conflict_paths: set[str] = field(default_factory=set)
    inputs: BuildInputs | None = None

    def manual_instructions(self, number: str = "<pr>") -> str:
        s = self.settings
        return f"""Merge the conflicting PRs together by hand and push the result as the
`{s.manual_branch}` branch, instead of labelling them:

```bash
git fetch origin main
git switch -c {s.manual_branch} origin/main
git fetch origin {s.manual_branch} && git merge FETCH_HEAD   # whatever is already there, if anything
git fetch origin pull/{number}/head && git merge FETCH_HEAD   # this one
git fetch origin pull/<other>/head && git merge FETCH_HEAD   # each one it clashes with
# resolve with whatever merge tool you like, then commit
git push origin {s.manual_branch}
```

Then drop the `{s.label}` label from the PRs that branch contains. Every preview run
merges `{s.manual_branch}` before it merges anything else, so your resolution is what lands.
There is one `{s.manual_branch}` branch for everyone, which is why the recipe merges it in
rather than replacing it: the push stays a fast-forward, and a rejected push means someone else got
there first — merge theirs in and push again.

The preview resets to plain `main` every Sunday, in the
{s.reset_hour:02d}:00 {s.reset_zone.key} hour: labels are removed and
`{s.manual_branch}` is deleted. Re-push the branch to bring it back."""

    async def comment_and_unlabel(self, pull: Pull, sha: str, paths: tuple[str, ...]) -> bool:
        s = self.settings
        marker = conflict_marker(sha, paths)
        listing = await gh_api(
            f"repos/{s.repo}/issues/{pull.number}/comments", "--paginate", "--slurp", check=False
        )
        if listing.code != 0:
            warn(f"could not list comments on #{pull.number}: {listing.first_line}")
            return False
        comments = COMMENT_PAGES.validate_json(listing.stdout)
        if not any(marker in comment.body for page in comments for comment in page):
            body = (
                f"{marker}\n### Left out of the {s.repo} preview\n\n"
                f"This PR could not be merged into the preview tree, so the preview was built without it "
                f"and the `{s.label}` label has been removed. Re-applying the label replays this conflict; "
                "the recipe below is the way in.\n\n"
            )
            if paths:
                body += "Conflicting files:\n\n"
                body += "".join(f"- `{path}`\n" for path in paths[:CONFLICT_LIMIT])
                if len(paths) > CONFLICT_LIMIT:
                    body += f"- …and {len(paths) - CONFLICT_LIMIT} more\n"
                body += "\n"
            body += self.manual_instructions(str(pull.number))
            posted = await gh_api(
                f"repos/{s.repo}/issues/{pull.number}/comments",
                "--method",
                "POST",
                "-f",
                f"body={body}",
                check=False,
            )
            if posted.code != 0:
                warn(f"could not comment on #{pull.number}: {posted.first_line}")
                return False
        removed = await gh_api(
            f"repos/{s.repo}/issues/{pull.number}/labels/{s.label}",
            "--method",
            "DELETE",
            check=False,
        )
        if removed.code != 0:
            warn(f"could not unlabel #{pull.number}: {removed.first_line}")
            return False
        return True

    async def skip_pull(self, pull: Pull, sha: str, outcome: Conflicted | Unmergeable) -> None:
        match outcome:
            case Conflicted(paths=paths, agent_note=None):
                reason = "merge conflict with the preview tree"
            case Conflicted(paths=paths, agent_note=note):
                reason = f"merge conflict with the preview tree; oswe left it out: {note}"
            case Unmergeable(reason=reason):
                paths = ()
        unlabelled = ""
        if await self.comment_and_unlabel(pull, sha, paths):
            unlabelled = f" — `{self.settings.label}` label removed"
        self.skipped.append(f"{pull.link} — {reason}{unlabelled}{listed_paths(paths)}")
        self.conflicted = self.conflicted or bool(paths)

    async def merge_manual_branch(self) -> None:
        branch = self.settings.manual_branch
        assert self.inputs is not None
        sha = self.inputs.manual_sha
        if sha is None:
            return
        match await merge(sha, f"preview: merge branch {branch}"):
            case Merged() as merged:
                self.conflict_paths.update(merged.paths)
                self.included.append(f"`{branch}` — `{sha[:7]}`{merged.suffix()}")
            case Conflicted(paths=paths):
                self.conflict_paths.update(paths)
                self.conflicted = True
                self.skipped.append(
                    f"`{branch}` — merge conflict with `main` — rebuild the branch{listed_paths(paths)}"
                )
            case Unmergeable(reason=reason):
                self.skipped.append(f"`{branch}` — {reason}")

    async def merge_pulls(self, defer_conflicts: bool) -> list[Pending]:
        s = self.settings
        assert self.inputs is not None
        pulls = sorted(self.inputs.pulls, key=lambda pull: pull.number)[: s.max_prs]
        pending: list[Pending] = []
        for pull in pulls:
            # Only someone with write access can push a branch into this repository, so
            # an in-repo head is the trust boundary; a fork's code stays out however the
            # PR is labelled. author_association would exclude members whose org
            # membership is private, since the workflow token cannot see it.
            head_repo = pull.head.repo.full_name if pull.head.repo else ""
            if head_repo != s.repo:
                self.skipped.append(
                    f"{pull.link} — head branch is in `{head_repo or 'a deleted fork'}`, not this repository"
                )
                continue
            ref = f"refs/preview-prs/{pull.number}"
            fetched = await git(
                "fetch", "--no-tags", "origin", f"pull/{pull.number}/head:{ref}", check=False
            )
            if fetched.code != 0:
                self.skipped.append(f"{pull.link} — could not fetch the PR head")
                continue
            sha = await rev_parse(ref)
            if sha != pull.head.sha:
                self.skipped.append(f"{pull.link} — head moved mid-run, rerun to pick it up")
                continue
            match await merge(sha, pull.merge_message):
                case Merged() as merged:
                    self.conflict_paths.update(merged.paths)
                    self.included.append(f"{pull.link} — `{sha[:7]}`{merged.suffix()}")
                case Conflicted(paths=paths) if defer_conflicts:
                    self.conflict_paths.update(paths)
                    pending.append(Pending(pull, sha, paths))
                case Conflicted() | Unmergeable() as failed:
                    if isinstance(failed, Conflicted):
                        self.conflict_paths.update(failed.paths)
                    await self.skip_pull(pull, sha, failed)
        return pending

    async def merge_pending(self, prompt: str, pending: list[Pending]) -> None:
        """Retry conflicting PRs on the finished tree, where rerere replays earlier resolutions."""
        remaining: list[Pending] = []
        for item in pending:
            match await merge(item.sha, item.pull.merge_message):
                case Merged() as merged:
                    self.conflict_paths.update(merged.paths)
                    self.included.append(f"{item.pull.link} — `{item.sha[:7]}`{merged.suffix()}")
                case Conflicted(paths=paths):
                    self.conflict_paths.update(paths)
                    remaining.append(Pending(item.pull, item.sha, paths))
                case Unmergeable() as failed:
                    await self.skip_pull(item.pull, item.sha, failed)
        if not remaining:
            return
        before = await rev_parse("HEAD")
        report = await resolve_with_agent(prompt, remaining, self.settings.agent_timeout_seconds)
        if report is not None:
            await self.consolidate_fixup(before)
        for item in remaining:
            number = item.pull.number
            if report is not None and number in report.merged:
                self.included.append(f"{item.pull.link} — `{item.sha[:7]}` — {AGENT_NOTE}")
            else:
                note = report.reasons.get(number) if report is not None else None
                await self.skip_pull(item.pull, item.sha, Conflicted(item.conflicts, note))

    async def consolidate_fixup(self, before: str) -> None:
        """Name only the agent's trailing non-merge commits as a replayable fix-up."""
        history = (await git("rev-list", "--first-parent", "--parents", f"{before}..HEAD")).stdout
        boundary = before
        trailing = False
        for line in history.splitlines():
            commit, *parents = line.split()
            if len(parents) > 1:
                boundary = commit
                break
            trailing = True
        if trailing:
            if await rev_parse("HEAD^{tree}") == await rev_parse(f"{boundary}^{{tree}}"):
                await restore_head(boundary)
            else:
                await git("reset", "-q", "--soft", boundary)
                await git("commit", "-q", "-m", FIXUP_MESSAGE)

    def write_summary(self, base_sha: str) -> None:
        skipped = list(dict.fromkeys(self.skipped))
        summary(
            "## Preview tree",
            "",
            f"Deployed preview: <{self.settings.url}>",
            f"Base: `main` @ `{base_sha[:7]}`",
            f"Republish even when the preview tree is unchanged: {'yes' if self.settings.force else 'no'}",
            "",
            f"### Merged ({len(self.included)})",
            "",
            *([f"- {entry}" for entry in self.included] or ["_preview is identical to main_"]),
            "",
            f"### Skipped ({len(skipped)})",
            "",
            *([f"- {entry}" for entry in skipped] or ["_nothing skipped_"]),
        )
        if self.conflicted:
            summary("", "### Getting a conflicting change in", "", self.manual_instructions())

    async def fetch_published(self) -> str | None:
        """Tree of the deployed preview branch, kept at ``PUBLISHED_REF``; None when there is none."""
        fetched = await git(
            "fetch",
            "--no-tags",
            "--force",
            "origin",
            f"{self.settings.branch}:{PUBLISHED_REF}",
            check=False,
        )
        return await rev_parse(f"{PUBLISHED_REF}^{{tree}}") if fetched.code == 0 else None

    async def reuse_fixup(self) -> None:
        """Replay the published oswe fix-up when it sits on exactly the tree just assembled."""
        history = (
            await git("log", "--first-parent", "--format=%H%x00%s", "-n", "20", PUBLISHED_REF)
        ).stdout
        commits = (line.split("\0", 1) for line in history.splitlines())
        tip = next((commit for commit in commits if commit[1] != FORCE_MESSAGE), None)
        if tip is None or tip[1] != FIXUP_MESSAGE:
            return
        fixup = tip[0]
        if await rev_parse(f"{fixup}^^{{tree}}") != await rev_parse("HEAD^{tree}"):
            return
        await git("cherry-pick", fixup)

    async def assemble(self, prompt: str | None) -> None:
        """Merge everything onto main; ``skipped`` accumulates, since a skipped PR loses its label."""
        self.included.clear()
        self.conflict_paths.clear()
        await restore_head("origin/main")
        await self.merge_manual_branch()
        pending = await self.merge_pulls(defer_conflicts=prompt is not None)
        if prompt is not None and pending:
            await self.merge_pending(prompt, pending)

    async def verify(
        self, prompt: str | None, rerere: RerereCache, published: str | None = None
    ) -> str | None:
        """Repair typecheck failures, discarding rerere only for remaining conflict errors."""
        if not self.settings.force and await remote_refs(FAILED_REF):
            await git("fetch", "--no-tags", "--force", "origin", f"{FAILED_REF}:{FAILED_REF}")
            if await rev_parse(f"{FAILED_REF}^{{tree}}") == await rev_parse("HEAD^{tree}"):
                return "Unchanged since an earlier run failed typecheck on this exact tree; see that run."
        errors = await typecheck()
        assembled = await rev_parse("HEAD")
        attempted_fix = False
        if errors and rerere.restored_tree is not None:
            if published is not None:
                await self.reuse_fixup()
                errors = await typecheck()
            if errors and prompt is not None:
                await fix_with_agent(
                    FIX_PROMPT_PATH.read_text(), errors, self.settings.agent_timeout_seconds
                )
                attempted_fix = True
                errors = await typecheck()
            if errors and prompt is not None and self.errors_touch_conflicts(errors):
                warn("conflict paths still fail typecheck; reassembling without the rerere cache")
                rerere.discard()
                await self.assemble(prompt)
                errors = await typecheck()
                assembled = await rev_parse("HEAD")
                attempted_fix = False
        if errors and prompt is not None and not attempted_fix:
            await fix_with_agent(
                FIX_PROMPT_PATH.read_text(), errors, self.settings.agent_timeout_seconds
            )
            errors = await typecheck()
        if errors:
            marked = await git(
                "push", "--force", "origin", f"{assembled}:{FAILED_REF}", check=False
            )
            if marked.code != 0:
                warn(f"could not record the failed preview tree: {marked.first_line}")
        return errors

    def errors_touch_conflicts(self, errors: str) -> bool:
        paths = (
            match.group(1).strip().removeprefix("./")
            for match in TYPECHECK_DIAGNOSTIC.finditer(errors)
        )
        return any(
            conflict == path or conflict.endswith(f"/{path}")
            for path in paths
            for conflict in self.conflict_paths
        )

    async def inputs_unchanged(self) -> bool:
        assert self.inputs is not None
        if self.settings.force or os.environ.get("GITHUB_EVENT_NAME") != "schedule":
            return False
        if not await remote_refs(INPUTS_REF):
            return False
        await git("fetch", "--no-tags", "--force", "origin", f"{INPUTS_REF}:{INPUTS_REF}")
        saved = (await git("log", "-1", "--format=%s", INPUTS_REF)).stdout.strip()
        return saved == self.inputs.fingerprint

    async def save_inputs(self) -> None:
        assert self.inputs is not None
        tree = await rev_parse("HEAD^{tree}")
        commit = (await git("commit-tree", tree, "-m", self.inputs.fingerprint)).stdout.strip()
        await git("push", "--force", "origin", f"{commit}:{INPUTS_REF}")

    async def publish(self, published: str | None) -> None:
        branch = self.settings.branch
        assembled = await rev_parse("HEAD^{tree}")
        if assembled == published and not self.settings.force:
            summary("", f"Preview tree unchanged (`{assembled[:7]}`) — nothing published.")
            set_output("changed", "false")
            await self.save_inputs()
            return
        if assembled == published:
            summary(
                "",
                f"Preview tree unchanged (`{assembled[:7]}`) — continuing because publication was forced.",
            )
            await git("commit", "--allow-empty", "-m", FORCE_MESSAGE)
        await git("push", "--force", "origin", f"HEAD:refs/heads/{branch}")
        await self.save_inputs()
        set_output("changed", "true")
        set_output("sha", await rev_parse("HEAD"))

    async def build(self) -> None:
        await git("config", "user.name", "github-actions[bot]")
        await git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        await git("config", "rerere.enabled", "true")
        await git("config", "rerere.autoUpdate", "true")
        await fetch_main()
        self.inputs = await BuildInputs.load(self.settings)
        if await self.inputs_unchanged():
            summary("inputs unchanged")
            set_output("changed", "false")
            return
        await git("checkout", "-B", self.settings.branch, "origin/main")
        base_sha = await rev_parse("HEAD")
        prompt = PROMPT_PATH.read_text() if shutil.which("oswe") else None
        rerere = RerereCache()
        await rerere.restore()
        published = await self.fetch_published()
        await self.assemble(prompt)
        if published is not None:
            await self.reuse_fixup()
        errors = None
        if self.settings.force or await rev_parse("HEAD^{tree}") != published:
            errors = await self.verify(prompt, rerere, published)
        await rerere.save()
        self.write_summary(base_sha)
        if errors:
            summary("", "### Typecheck failed — nothing published", "", "```", errors, "```")
            raise PreviewError("the preview tree fails typecheck; nothing was published")
        await self.publish(published)

    async def reset(self) -> None:
        s = self.settings
        now = datetime.now(s.reset_zone)
        if now.weekday() != 6 or now.hour != s.reset_hour:
            print(f"{now:%A %H:%M} {s.reset_zone.key} is outside the Sunday reset hour.")
            return
        today = now.date()
        resets = sorted(
            ref.removeprefix(RESET_REF_PREFIX) for ref in await remote_refs(f"{RESET_REF_PREFIX}*")
        )
        if resets and date.fromisoformat(resets[-1]) >= today:
            print(f"The {resets[-1]} reset already covers today.")
            return

        summary("## Sunday reset", "")
        incomplete = False
        try:
            pulls = [pull for pull in await open_pulls(s.repo) if pull.has_label(s.label)]
        except PreviewError as exc:
            summary("- **no PR was unlabelled** — could not list open pull requests")
            warn(str(exc))
            incomplete = True
            pulls = []
        for pull in pulls:
            link = f"[#{pull.number} {pull.title}]({pull.html_url})"
            removed = await gh_api(
                f"repos/{s.repo}/issues/{pull.number}/labels/{s.label}",
                "--method",
                "DELETE",
                check=False,
            )
            if removed.code == 0:
                summary(f"- dropped `{s.label}` from {link}")
            else:
                summary(f"- **kept `{s.label}` on {link}** — removal failed")
                incomplete = True

        match await remote_branch_exists(s.manual_branch):
            case True:
                if (
                    await git("push", "origin", "--delete", s.manual_branch, check=False)
                ).code == 0:
                    summary(f"- deleted `{s.manual_branch}`")
                else:
                    summary(f"- **kept `{s.manual_branch}`** — deletion failed")
                    incomplete = True
            case False:
                summary(f"_no `{s.manual_branch}` branch_")
            case None:
                summary(f"- **`{s.manual_branch}` may remain** — branch lookup failed")
                incomplete = True
        if incomplete:
            print(f"Reset incomplete — leaving the {today} marker unset so the next tick retries.")
            return
        await fetch_main()
        marker = f"{RESET_REF_PREFIX}{today}"
        await git("push", "origin", f"origin/main:{marker}")
        for ref in await remote_refs(f"{RESET_REF_PREFIX}*"):
            if (
                ref != marker
                and (await git("push", "origin", "--delete", ref, check=False)).code != 0
            ):
                warn(f"could not delete {ref}")


async def main(command: str) -> None:
    preview = Preview(Settings.from_env())
    if command == "reset":
        await preview.reset()
    else:
        await preview.build()


if __name__ == "__main__":
    match sys.argv[1:]:
        case [] | ["build"]:
            command = "build"
        case ["reset"]:
            command = "reset"
        case _:
            print(f"usage: {sys.argv[0]} [build|reset]", file=sys.stderr)
            sys.exit(2)
    try:
        asyncio.run(main(command))
    except PreviewError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
