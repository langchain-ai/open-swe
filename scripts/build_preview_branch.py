# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic>=2.12"]
# ///
"""Assemble the preview branch from main, preview-manual and labelled PRs, or reset it."""

import asyncio
import hashlib
import os
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
# Where Open SWE sits in the repository this runs in: "" when it is the root, a
# subdirectory when it lives in a monorepo. Git state stays at the repository root.
PROJECT_DIR = os.environ.get("PREVIEW_PROJECT_DIR", "").strip("/")
FORCE_MESSAGE = "preview: force deployment"
WORKFLOWS_MESSAGE = "preview: keep the published workflows"
# GitHub refuses a push by the workflow's token that changes anything here.
WORKFLOWS = ".github/workflows"
TYPECHECK_IMAGE = "node:26-bookworm-slim"
TYPECHECK_SCRIPT = (
    "npx --yes pnpm@11.22.0 install --frozen-lockfile --ignore-scripts --ignore-pnpmfile"
    " && npx --yes pnpm@11.22.0 --filter open-swe-dashboard run typecheck"
)
TYPECHECK_OUTPUT_LIMIT = 20_000
FAILED_REF = "refs/preview-failed"
PUBLISHED_REF = "refs/preview-published"
RERERE_NOTE = "conflicts resolved from the rerere cache"
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

    def suffix(self) -> str:
        return f" — {self.note}" if self.note else ""


@dataclass(frozen=True)
class Conflicted:
    paths: tuple[str, ...]


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
    merging = (await git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)).code == 0
    if (
        merging
        and not conflicts
        and (await git("commit", "-q", "--no-edit", check=False)).code == 0
    ):
        await discard_uncommitted()
        return Merged(RERERE_NOTE)
    if conflicts:
        await restore_head(before)
        return Conflicted(conflicts)
    unrelated = (await git("merge-base", "HEAD", sha, check=False)).code != 0
    await git("merge", "--abort", check=False)
    if unrelated:
        return Unmergeable(f"could not be merged — {result.first_line}")
    raise PreviewError(f"merge {sha[:7]} failed: {result.stderr or result.stdout}")


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
        tree = f"HEAD:{PROJECT_DIR}" if PROJECT_DIR else "HEAD"
        await git("archive", "--format=tar", "-o", str(archive), tree)
        await run("tar", "-xf", str(archive), "-C", str(source))
        result = await run(
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/tmp",
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
            case Conflicted(paths=paths):
                reason = "merge conflict with the preview tree"
            case Unmergeable(reason=reason):
                paths = ()
        unlabelled = ""
        if await self.comment_and_unlabel(pull, sha, paths):
            unlabelled = f" — `{self.settings.label}` label removed"
        self.skipped.append(f"{pull.link} — {reason}{unlabelled}{listed_paths(paths)}")
        self.conflicted = self.conflicted or bool(paths)

    async def merge_manual_branch(self) -> None:
        branch = self.settings.manual_branch
        exists = await remote_branch_exists(branch)
        if exists is None:
            raise PreviewError(f"could not look up {branch}")
        if not exists:
            return
        ref = "refs/preview-manual"
        fetched = await git(
            "fetch", "--no-tags", "--force", "origin", f"refs/heads/{branch}:{ref}", check=False
        )
        if fetched.code != 0:
            reason = f"could not fetch the branch: {fetched.first_line}"
        else:
            sha = await rev_parse(ref)
            match await merge(sha, f"preview: merge branch {branch}"):
                case Merged() as merged:
                    self.included.append(f"`{branch}` — `{sha[:7]}`{merged.suffix()}")
                    return
                case Conflicted(paths=paths):
                    reason = f"merge conflict with `main` — rebuild the branch{listed_paths(paths)}"
                case Unmergeable(reason=reason):
                    pass
        summary(
            "## Preview build failed — nothing published",
            "",
            f"`{branch}` — {reason}",
            "",
            f"Repair `{branch}` and rerun the build. The preview cannot be deployed without it.",
        )
        raise PreviewError(f"{branch}: {reason}; nothing was published")

    async def merge_pulls(self) -> None:
        s = self.settings
        pulls = sorted(
            (pull for pull in await open_pulls(s.repo) if pull.has_label(s.label)),
            key=lambda pull: pull.number,
        )[: s.max_prs]
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
                    self.included.append(f"{pull.link} — `{sha[:7]}`{merged.suffix()}")
                case Conflicted() | Unmergeable() as failed:
                    await self.skip_pull(pull, sha, failed)

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

    async def assemble(self) -> None:
        """Merge everything onto main; ``skipped`` accumulates, since a skipped PR loses its label."""
        self.included.clear()
        await restore_head("origin/main")
        await self.merge_manual_branch()
        await self.merge_pulls()

    async def verify(self) -> str | None:
        """Typecheck errors left in the tree about to publish; None when it is clean."""
        if not self.settings.force and await remote_refs(FAILED_REF):
            await git("fetch", "--no-tags", "--force", "origin", f"{FAILED_REF}:{FAILED_REF}")
            if await rev_parse(f"{FAILED_REF}^{{tree}}") == await rev_parse("HEAD^{tree}"):
                return "Unchanged since an earlier run failed typecheck on this exact tree; see that run."
        errors = await typecheck()
        assembled = await rev_parse("HEAD")
        if errors:
            marked = await git(
                "push", "--force", "origin", f"{assembled}:{FAILED_REF}", check=False
            )
            if marked.code != 0:
                warn(f"could not record the failed preview tree: {marked.first_line}")
        return errors

    async def keep_published_workflows(self) -> None:
        """Put back the published preview's workflow files where the tree changed any.

        The workflow's token cannot push a change to a workflow file, so a tree that
        changes one, such as main editing CI, could never be published. The preview
        branch's copies never run: every workflow runs from main's.
        """
        unchanged = await git(
            "diff", "--quiet", PUBLISHED_REF, "HEAD", "--", WORKFLOWS, check=False
        )
        if unchanged.code == 0:
            return
        await git("rm", "-r", "-q", "--ignore-unmatch", "--", WORKFLOWS)
        if (await git("ls-tree", "-d", PUBLISHED_REF, WORKFLOWS)).stdout.strip():
            await git("checkout", PUBLISHED_REF, "--", WORKFLOWS)
        await git("commit", "-q", "-m", WORKFLOWS_MESSAGE)
        summary(
            "",
            "Kept the published preview's workflow files: the build cannot push changes to them.",
        )

    async def publish(self, published: str | None) -> None:
        branch = self.settings.branch
        assembled = await rev_parse("HEAD^{tree}")
        if assembled == published and not self.settings.force:
            summary("", f"Preview tree unchanged (`{assembled[:7]}`) — nothing published.")
            set_output("changed", "false")
            return
        if assembled == published:
            summary(
                "",
                f"Preview tree unchanged (`{assembled[:7]}`) — continuing because publication was forced.",
            )
            await git("commit", "--allow-empty", "-m", FORCE_MESSAGE)
        await git("push", "--force", "origin", f"HEAD:refs/heads/{branch}")
        set_output("changed", "true")
        set_output("sha", await rev_parse("HEAD"))

    async def build(self) -> None:
        await git("config", "user.name", "github-actions[bot]")
        await git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        await git("config", "rerere.enabled", "true")
        await git("config", "rerere.autoUpdate", "true")
        await fetch_main()
        await git("checkout", "-B", self.settings.branch, "origin/main")
        base_sha = await rev_parse("HEAD")
        rerere = RerereCache()
        await rerere.restore()
        published = await self.fetch_published()
        await self.assemble()
        if published is not None:
            # Before the unchanged-tree check: the published tree keeps its own workflows.
            await self.keep_published_workflows()
        errors = None
        if self.settings.force or await rev_parse("HEAD^{tree}") != published:
            errors = await self.verify()
        await rerere.save()
        self.write_summary(base_sha)
        if errors:
            summary("", "### Typecheck failed — nothing published", "", "```", errors, "```")
            raise PreviewError("the preview tree fails typecheck; nothing was published")
        if published is not None:
            await self.keep_published_workflows()
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
