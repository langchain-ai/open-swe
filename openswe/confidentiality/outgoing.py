"""Collect what a push or a GitHub CLI write would publish, as segments the check reads."""

import re
import shlex
from dataclasses import dataclass

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.confidentiality.scan import Segment
from openswe.middleware.git_push import PushInspectionError, PushRange, run_git
from openswe.middleware.pr_creation_guard import executable_name

_COMMIT_SEPARATOR = "\x1e"
_MAX_DIFF_CHARS = 2_000_000
_GH_WRITES = {
    ("pr", "create"),
    ("pr", "edit"),
    ("pr", "comment"),
    ("pr", "review"),
    ("issue", "create"),
    ("issue", "edit"),
    ("issue", "comment"),
    ("release", "create"),
    ("release", "edit"),
}
_GH_TEXT_FLAGS = {"-t", "--title", "-b", "--body", "-n", "--notes"}
_GH_FILE_FLAGS = {"-F", "--body-file", "--notes-file"}
_GH_REPO_FLAGS = {"-R", "--repo"}
_GH_API_FIELD_FLAGS = {"-f", "--raw-field", "-F", "--field"}
_GH_API_METHOD_FLAGS = {"-X", "--method"}
_GH_API_REPO = re.compile(r"(?:^|/)repos/([^/\s]+)/([^/\s]+)/")
_GITHUB_URL_REPO = re.compile(r"https://github\.com/([^/\s]+)/([^/\s#?]+)")
_SEPARATORS = {";", "&&", "||", "|", "&"}


async def push_segments(backend: SandboxBackendProtocol, pushed: PushRange) -> tuple[Segment, ...]:
    """The branch name, commit messages and authors, and every added line ``pushed`` publishes."""
    segments = [Segment("branch name", pushed.branch)]
    log = await run_git(
        backend,
        pushed.root,
        f"log --no-color --format=%h%x00%an%x20%ae%x00%B{_COMMIT_SEPARATOR} {pushed.range_expr}",
    )
    diff = await run_git(
        backend, pushed.root, f"diff --no-color --no-ext-diff --unified=0 {pushed.range_expr}"
    )
    if not log.ok or not diff.ok or len(diff.output) > _MAX_DIFF_CHARS:
        raise PushInspectionError(
            "Cannot read what this push would publish to a public repository. Nothing ran."
        )
    for entry in log.output.split(_COMMIT_SEPARATOR):
        sha, _, rest = entry.strip().partition("\x00")
        if sha:
            segments.append(Segment(f"commit {sha}", rest.replace("\x00", "\n")))
    segments.extend(_added_lines(diff.output))
    return tuple(segments)


def _added_lines(diff: str) -> list[Segment]:
    files: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            current = None if path == "/dev/null" else files.setdefault(path, [path])
        elif line.startswith("+") and current is not None:
            current.append(line[1:])
    return [Segment(f"file {path}", "\n".join(lines)) for path, lines in files.items()]


@dataclass(frozen=True)
class GitHubCliWrite:
    """Text a ``gh`` command would post, where it would post it, and files it reads it from."""

    repository: str | None
    repo_dir: str | None
    segments: tuple[Segment, ...]
    body_files: tuple[str, ...]

    async def with_body_files(self, backend: SandboxBackendProtocol) -> tuple[Segment, ...]:
        segments = list(self.segments)
        for path in self.body_files:
            if path == "-":
                raise PushInspectionError(
                    "Cannot inspect a GitHub CLI body read from standard input. Write it to a "
                    "file or pass it inline. Nothing ran."
                )
            cd = f"cd {shlex.quote(self.repo_dir)} && " if self.repo_dir else ""
            response = await backend.aexecute(f"{cd}cat -- {shlex.quote(path)}", timeout=30)
            if response.exit_code not in (0, None):
                raise PushInspectionError(f"Cannot read {path} to inspect it. Nothing ran.")
            segments.append(Segment(f"gh body file {path}", response.output))
        return tuple(segments)


def github_cli_writes(tokens: list[str]) -> list[GitHubCliWrite]:
    """Every ``gh`` invocation in ``tokens`` that posts text to GitHub."""
    writes: list[GitHubCliWrite] = []
    cwd: str | None = None
    for index, token in enumerate(tokens):
        if token == "cd" and index + 1 < len(tokens):
            cwd = tokens[index + 1]
        if executable_name(token) != "gh":
            continue
        args: list[str] = []
        for candidate in tokens[index + 1 :]:
            if candidate in _SEPARATORS:
                break
            args.append(candidate)
        write = _cli_write(args, cwd)
        if write is not None:
            writes.append(write)
    return writes


def _cli_write(args: list[str], cwd: str | None) -> GitHubCliWrite | None:
    if len(args) >= 2 and (args[0], args[1]) in _GH_WRITES:
        return _subcommand_write(args, cwd)
    if args and args[0] == "api":
        return _api_write(args[1:], cwd)
    return None


def _flag_values(args: list[str], flags: set[str]) -> list[str]:
    values: list[str] = []
    for index, arg in enumerate(args):
        name, equals, value = arg.partition("=")
        if equals and name in flags and name.startswith("--"):
            values.append(value)
        elif arg in flags and index + 1 < len(args):
            values.append(args[index + 1])
    return values


def _url_repository(args: list[str]) -> str | None:
    for arg in args:
        if match := _GITHUB_URL_REPO.search(arg):
            return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"
    return None


def _subcommand_write(args: list[str], cwd: str | None) -> GitHubCliWrite:
    location = f"gh {args[0]} {args[1]}"
    repo_flags = _flag_values(args, _GH_REPO_FLAGS)
    return GitHubCliWrite(
        repository=repo_flags[0] if repo_flags else _url_repository(args),
        repo_dir=cwd,
        segments=tuple(Segment(location, value) for value in _flag_values(args, _GH_TEXT_FLAGS)),
        body_files=tuple(_flag_values(args, _GH_FILE_FLAGS)),
    )


def _api_write(args: list[str], cwd: str | None) -> GitHubCliWrite | None:
    fields = _flag_values(args, _GH_API_FIELD_FLAGS)
    methods = [method.upper() for method in _flag_values(args, _GH_API_METHOD_FLAGS)]
    inputs = _flag_values(args, {"--input"})
    if "GET" in methods or (
        not fields and not inputs and not any(m in {"POST", "PATCH", "PUT"} for m in methods)
    ):
        return None
    endpoint = next((arg for arg in args if _GH_API_REPO.search(arg)), "")
    match = _GH_API_REPO.search(endpoint)
    segments = []
    body_files = list(inputs)
    for field in fields:
        name, _, value = field.partition("=")
        if value.startswith("@"):
            body_files.append(value[1:])
        else:
            segments.append(Segment(f"gh api {name}", value))
    return GitHubCliWrite(
        repository=f"{match.group(1)}/{match.group(2)}" if match else None,
        repo_dir=cwd,
        segments=tuple(segments),
        body_files=tuple(body_files),
    )
