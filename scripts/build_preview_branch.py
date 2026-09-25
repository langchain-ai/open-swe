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
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, TypeAdapter

CONFLICT_LIMIT = 10
PROMPT_PATH = Path(".github/prompts/resolve_preview_conflict.md")
MERGED_LINE = re.compile(r"merged:((?: \d+)*)")
LEFT_OUT_LINE = re.compile(r"#(\d+): (.+)")
AGENT_INTERRUPT_GRACE_SECONDS = 60
RERERE_NOTE = "conflicts resolved from the rerere cache"
AGENT_NOTE = "conflicts resolved by oswe"
RESET_REF_PREFIX = "refs/preview-reset/"


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
    reset_days: int
    reset_hour: int
    reset_zone: ZoneInfo
    url: str
    agent_timeout_seconds: float
    excluded_pr: int | None
    force: bool

    @classmethod
    def from_env(cls) -> Self:
        excluded = os.environ.get("EXCLUDE_PR_NUMBER", "")
        if excluded and not excluded.isdigit():
            raise PreviewError(f"invalid EXCLUDE_PR_NUMBER: {excluded}")
        return cls(
            repo=os.environ["GH_REPO"],
            branch=_env("PREVIEW_BRANCH", "preview"),
            label=_env("PREVIEW_LABEL", "preview"),
            manual_branch=_env("PREVIEW_MANUAL_BRANCH", "preview-manual"),
            max_prs=int(_env("PREVIEW_MAX_PRS", "50")),
            reset_days=int(_env("PREVIEW_RESET_DAYS", "7")),
            reset_hour=int(_env("PREVIEW_RESET_HOUR", "7")),
            reset_zone=ZoneInfo(_env("PREVIEW_RESET_ZONE", "America/New_York")),
            url=_env(
                "PREVIEW_URL",
                "https://open-swe-preview-cc53e8fbe667565d843d0843f84ee92c.us.langgraph.app/agents",
            ),
            agent_timeout_seconds=float(_env("PREVIEW_AGENT_TIMEOUT_SECONDS", "1800")),
            excluded_pr=int(excluded) if excluded else None,
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


async def run(*args: str, check: bool = True) -> Completed:
    proc = await asyncio.create_subprocess_exec(*args, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    result = Completed(await proc.wait(), stdout.decode(), stderr.decode())
    if check and result.code != 0:
        raise PreviewError(f"{' '.join(args)} exited {result.code}: {result.stderr.strip()}")
    return result


async def git(*args: str, check: bool = True) -> Completed:
    return await run("git", *args, check=check)


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


@dataclass(frozen=True)
class Merged:
    note: str | None = None

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


async def resolve_with_agent(
    prompt: str, pending: list[Pending], timeout: float
) -> AgentReport | None:
    """Hand every conflicting PR to one oswe run and return what it reports doing."""
    before = await rev_parse("HEAD")
    listing = "\n".join(f"#{item.pull.number} {item.sha} {item.pull.title}" for item in pending)
    print(f"merging {len(pending)} conflicting PR(s) with oswe", file=sys.stderr, flush=True)
    proc = await asyncio.create_subprocess_exec("oswe", "run", prompt, stdin=PIPE, stdout=PIPE)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(listing.encode()), timeout)
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
    if parsed := AgentReport.parse(report):
        return parsed
    warn(
        f"oswe exited {proc.returncode} without a report in the required format; discarding its work"
    )
    await restore_head(before)
    return None


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
    agent_resolved: bool = False

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

The preview resets to plain `main` every {s.reset_days} days, in the
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
            self.skipped.append(f"`{branch}` — could not fetch the branch")
            return
        sha = await rev_parse(ref)
        match await merge(sha, f"preview: merge branch {branch}"):
            case Merged() as merged:
                self.included.append(f"`{branch}` — `{sha[:7]}`{merged.suffix()}")
            case Conflicted(paths=paths):
                self.conflicted = True
                self.skipped.append(
                    f"`{branch}` — merge conflict with `main` — rebuild the branch{listed_paths(paths)}"
                )
            case Unmergeable(reason=reason):
                self.skipped.append(f"`{branch}` — {reason}")

    async def merge_pulls(self, defer_conflicts: bool) -> list[Pending]:
        s = self.settings
        pulls = sorted(
            (
                pull
                for pull in await open_pulls(s.repo)
                if pull.has_label(s.label) and pull.number != s.excluded_pr
            ),
            key=lambda pull: pull.number,
        )[: s.max_prs]
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
                    self.included.append(f"{pull.link} — `{sha[:7]}`{merged.suffix()}")
                case Conflicted(paths=paths) if defer_conflicts:
                    pending.append(Pending(pull, sha, paths))
                case Conflicted() | Unmergeable() as failed:
                    await self.skip_pull(pull, sha, failed)
        return pending

    async def merge_pending(self, prompt: str, pending: list[Pending]) -> None:
        """Retry conflicting PRs on the finished tree, where rerere replays earlier resolutions."""
        remaining: list[Pending] = []
        for item in pending:
            match await merge(item.sha, item.pull.merge_message):
                case Merged() as merged:
                    self.included.append(f"{item.pull.link} — `{item.sha[:7]}`{merged.suffix()}")
                case Conflicted(paths=paths):
                    remaining.append(Pending(item.pull, item.sha, paths))
                case Unmergeable() as failed:
                    await self.skip_pull(item.pull, item.sha, failed)
        if not remaining:
            return
        report = await resolve_with_agent(prompt, remaining, self.settings.agent_timeout_seconds)
        for item in remaining:
            number = item.pull.number
            if report is not None and number in report.merged:
                self.included.append(f"{item.pull.link} — `{item.sha[:7]}` — {AGENT_NOTE}")
                self.agent_resolved = True
            else:
                note = report.reasons.get(number) if report is not None else None
                await self.skip_pull(item.pull, item.sha, Conflicted(item.conflicts, note))

    def write_summary(self, base_sha: str) -> None:
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
            f"### Skipped ({len(self.skipped)})",
            "",
            *([f"- {entry}" for entry in self.skipped] or ["_nothing skipped_"]),
        )
        if self.conflicted:
            summary("", "### Getting a conflicting change in", "", self.manual_instructions())

    async def publish(self) -> None:
        branch = self.settings.branch
        assembled = await rev_parse("HEAD^{tree}")
        published = None
        fetched = await git(
            "fetch",
            "--no-tags",
            "--force",
            "origin",
            f"{branch}:refs/preview-published",
            check=False,
        )
        if fetched.code == 0:
            published = await rev_parse("refs/preview-published^{tree}")
        if assembled == published and not self.settings.force:
            summary("", f"Preview tree unchanged (`{assembled[:7]}`) — nothing published.")
            set_output("changed", "false")
            return
        if assembled == published:
            summary(
                "",
                f"Preview tree unchanged (`{assembled[:7]}`) — continuing because publication was forced.",
            )
            await git("commit", "--allow-empty", "-m", "preview: force deployment")
        await git("push", "--force", "origin", f"HEAD:refs/heads/{branch}")
        set_output("changed", "true")

    async def build(self) -> None:
        await git("config", "user.name", "github-actions[bot]")
        await git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        await git("config", "rerere.enabled", "true")
        await git("config", "rerere.autoUpdate", "true")
        await fetch_main()
        await git("checkout", "-B", self.settings.branch, "origin/main")
        base_sha = await rev_parse("HEAD")
        prompt = PROMPT_PATH.read_text() if shutil.which("oswe") else None
        await self.merge_manual_branch()
        pending = await self.merge_pulls(defer_conflicts=prompt is not None)
        if prompt is not None and pending:
            await self.merge_pending(prompt, pending)
        set_output("agent_resolved", "true" if self.agent_resolved else "false")
        self.write_summary(base_sha)
        await self.publish()

    async def reset(self) -> None:
        s = self.settings
        now = datetime.now(s.reset_zone)
        if now.hour != s.reset_hour:
            print(f"{now:%H:%M} {s.reset_zone.key} is outside the reset hour.")
            return
        today = now.date()
        resets = sorted(
            ref.removeprefix(RESET_REF_PREFIX) for ref in await remote_refs(f"{RESET_REF_PREFIX}*")
        )
        if resets and (today - date.fromisoformat(resets[-1])).days < s.reset_days:
            print(f"The {resets[-1]} reset was less than {s.reset_days} days ago.")
            return

        summary(f"## {s.reset_days}-day reset", "")
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
