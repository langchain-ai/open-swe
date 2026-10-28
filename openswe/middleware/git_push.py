"""Read a standalone ``git push`` and inspect what it would publish from the thread's box."""

import logging
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openswe.middleware.pr_creation_guard import executable_name, shell_tokens

logger = logging.getLogger(__name__)

_SHELL_OPERATORS = {";", "|", "||", "&"}
_REF_NAME = re.compile(r"^[A-Za-z0-9._/@+-]+$")
GIT_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40,64}$")
_UNSAFE_RAW_COMMAND = re.compile(r"[;|`$<>\n\r]")
# What deepagents' local shell backend reports for a command that printed nothing.
_NO_OUTPUT = "<no output>"


class PushInspectionError(RuntimeError):
    """A push that must be inspected could not be inspected safely."""


@dataclass(frozen=True)
class GitInspectResult:
    output: str
    ok: bool


@dataclass(frozen=True)
class ParsedGitPush:
    repo_dir: str | None
    remote: str
    local_ref: str
    remote_ref: str
    set_upstream: bool = False
    force_with_lease: str | None = None

    @property
    def remote_branch(self) -> str:
        return self.remote_ref.removeprefix("refs/heads/")

    def inspection_failed(self) -> None:
        if self.force_with_lease is not None:
            raise PushInspectionError(
                "Cannot inspect this lease-protected push. Push the current branch to the "
                "same branch on origin and ensure the repository can be inspected. Nothing ran."
            )

    def command_for_head(self, repo_dir: str, head_sha: str) -> str:
        args = ["push"]
        if self.set_upstream:
            args.append("--set-upstream")
        if self.force_with_lease is not None:
            args.append(self.force_with_lease)
        args.extend([self.remote, f"{head_sha}:refs/heads/{self.remote_branch}"])
        return git_command(repo_dir, " ".join(shlex.quote(arg) for arg in args))


@dataclass(frozen=True)
class PushRange:
    """The commits a push would publish: ``range_expr`` from ``base_sha`` to ``head``."""

    root: str
    branch: str
    head: str
    base_sha: str
    range_expr: str


def _response_output(response: Any) -> str:
    output = getattr(response, "output", None)
    if not isinstance(output, str):
        value = response.get("output") if isinstance(response, Mapping) else None
        output = value if isinstance(value, str) else str(response or "")
    return "" if output.strip() == _NO_OUTPUT else output


def _response_ok(response: Any) -> bool:
    exit_code = getattr(response, "exit_code", None)
    if isinstance(exit_code, int):
        return exit_code == 0
    if isinstance(response, Mapping):
        value = response.get("exit_code")
        if isinstance(value, int):
            return value == 0
    return True


def parse_git_push(command: str) -> ParsedGitPush | None:
    """``command`` as a standalone push to ``origin``, or ``None`` if it is anything else."""
    stripped = command.strip()
    if _UNSAFE_RAW_COMMAND.search(stripped) or "&" in stripped.replace("&&", ""):
        return None
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None
    if not tokens:
        return None

    if len(tokens) >= 4 and tokens[0] == "cd" and tokens[2] == "&&":
        if any(token in _SHELL_OPERATORS or token == "&&" for token in tokens[3:]):
            return None
        return _parse_git_tokens(tokens[3:], repo_dir=tokens[1])

    if any(token in _SHELL_OPERATORS or token == "&&" for token in tokens):
        return None
    return _parse_git_tokens(tokens, repo_dir=None)


def _parse_git_tokens(tokens: list[str], *, repo_dir: str | None) -> ParsedGitPush | None:
    if not tokens or tokens[0] != "git":
        return None
    i = 1
    while i < len(tokens) and tokens[i] != "push":
        if tokens[i] == "-C" and i + 1 < len(tokens):
            repo_dir = tokens[i + 1]
            i += 2
            continue
        return None
    if i >= len(tokens) or tokens[i] != "push":
        return None
    return _parse_push_args(tokens[i + 1 :], repo_dir=repo_dir)


def _parse_push_args(tokens: list[str], *, repo_dir: str | None) -> ParsedGitPush | None:
    set_upstream = False
    force_with_lease: str | None = None
    positional: list[str] = []
    for token in tokens:
        if token in {"-u", "--set-upstream"}:
            set_upstream = True
        elif token == "--force-with-lease" or token.startswith("--force-with-lease="):
            if force_with_lease is not None or not _safe_force_with_lease(token):
                return None
            force_with_lease = token
        elif token.startswith("-"):
            return None
        else:
            positional.append(token)
    if len(positional) != 2 or positional[0] != "origin":
        return None
    parsed = _parse_refspec(positional[1])
    if parsed is None:
        return None
    local_ref, remote_ref = parsed
    return ParsedGitPush(
        repo_dir=repo_dir,
        remote="origin",
        local_ref=local_ref,
        remote_ref=remote_ref,
        set_upstream=set_upstream,
        force_with_lease=force_with_lease,
    )


def _safe_force_with_lease(option: str) -> bool:
    if option == "--force-with-lease":
        return True
    ref, separator, expected = option.partition("=")[2].partition(":")
    return _safe_ref(ref, allow_head=False) and (
        not separator or not expected or bool(GIT_OBJECT_ID.fullmatch(expected))
    )


def _parse_refspec(refspec: str) -> tuple[str, str] | None:
    if refspec.startswith("-") or ".." in refspec:
        return None
    if ":" in refspec:
        parts = refspec.split(":")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None
        local_ref, remote_ref = parts
    else:
        local_ref = remote_ref = refspec
    if not _safe_ref(local_ref, allow_head=True) or not _safe_ref(remote_ref, allow_head=False):
        return None
    return local_ref, remote_ref


def _safe_ref(ref: str, *, allow_head: bool) -> bool:
    if allow_head and ref == "HEAD":
        return True
    if ref == "HEAD" or not _REF_NAME.fullmatch(ref):
        return False
    return not any(part in {"", ".", ".."} for part in ref.split("/"))


# Control operators, which a shell honors with or without surrounding spaces.
_SHELL_OPERATOR = re.compile(r"(&&|\|\||[;&|()])")


def _command_words(command: str) -> list[str]:
    """``command``'s words, with control operators split off even where written unspaced."""
    return [
        part for token in shell_tokens(command) for part in _SHELL_OPERATOR.split(token) if part
    ]


def chained_push_dirs(command: str) -> list[str | None]:
    """The repositories ``command`` pushes from where the push is not the whole command.

    A push is located by ``git -C dir push`` or the ``cd dir`` before it; ``None``
    is the shell's own working directory.
    """
    if parse_git_push(command) is not None:
        return []
    tokens = _command_words(command)
    dirs: list[str | None] = []
    cwd: str | None = None
    for index, token in enumerate(tokens):
        if token == "cd" and index + 1 < len(tokens):
            cwd = tokens[index + 1]
            continue
        if executable_name(token) != "git":
            continue
        repo_dir = cwd
        position = index + 1
        while position < len(tokens) and tokens[position].startswith("-"):
            if tokens[position] in {"-C", "-c"} and position + 1 < len(tokens):
                if tokens[position] == "-C":
                    repo_dir = tokens[position + 1]
                position += 2
                continue
            position += 1
        if position < len(tokens) and tokens[position] == "push":
            dirs.append(repo_dir)
    return dirs


def git_command(repo_dir: str | None, args: str) -> str:
    if repo_dir:
        return f"git -C {shlex.quote(repo_dir)} {args}"
    return f"git {args}"


async def run_git(backend: Any, repo_dir: str | None, args: str) -> GitInspectResult:
    try:
        response = await backend.aexecute(git_command(repo_dir, args), timeout=30)
    except Exception:
        logger.debug("push inspection failed for git %s", args, exc_info=True)
        return GitInspectResult("", False)
    return GitInspectResult(_response_output(response).strip(), _response_ok(response))


def first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def normalize_remote(remote: str) -> str:
    value = remote.strip()
    if value.endswith(".git"):
        value = value[:-4]
    value = re.sub(r"^https://[^/@]+@github\.com/", "https://github.com/", value)
    value = re.sub(r"^git@github\.com:", "https://github.com/", value)
    return value


async def push_range(backend: Any, parsed: ParsedGitPush) -> PushRange | None:
    """What ``parsed`` would publish, or ``None`` when the box cannot show it."""
    root_result = await run_git(backend, parsed.repo_dir, "rev-parse --show-toplevel")
    root = first_line(root_result.output) if root_result.ok else ""
    if not root:
        return parsed.inspection_failed()

    branch = await run_git(backend, root, "rev-parse --abbrev-ref HEAD")
    branch_name = first_line(branch.output) if branch.ok else ""
    if not branch_name or branch_name == "HEAD" or parsed.remote_branch != branch_name:
        return parsed.inspection_failed()
    if parsed.local_ref not in {"HEAD", branch_name, f"refs/heads/{branch_name}"}:
        return parsed.inspection_failed()

    target_sha = await run_git(backend, root, f"rev-parse {shlex.quote(parsed.local_ref)}")
    head = first_line(target_sha.output) if target_sha.ok else ""
    if not head or not GIT_OBJECT_ID.fullmatch(head):
        return parsed.inspection_failed()

    remote_branch = f"refs/remotes/{parsed.remote}/{parsed.remote_branch}"
    remote_branch_exists = await run_git(
        backend, root, f"rev-parse --verify {shlex.quote(remote_branch)}"
    )
    if remote_branch_exists.ok and first_line(remote_branch_exists.output):
        range_expr = f"{shlex.quote(remote_branch)}..{shlex.quote(head)}"
        base_result = await run_git(backend, root, f"rev-parse {shlex.quote(remote_branch)}")
        base_sha = first_line(base_result.output)
    else:
        origin_head = await run_git(backend, root, "symbolic-ref --short refs/remotes/origin/HEAD")
        base_ref = first_line(origin_head.output) if origin_head.ok else "origin/main"
        range_expr = f"{shlex.quote(base_ref)}...{shlex.quote(head)}"
        merge_base = await run_git(
            backend, root, f"merge-base {shlex.quote(head)} {shlex.quote(base_ref)}"
        )
        base_sha = first_line(merge_base.output)
    return PushRange(
        root=root, branch=branch_name, head=head, base_sha=base_sha, range_expr=range_expr
    )


async def origin_repository(backend: Any, root: str) -> str | None:
    """``owner/name`` of the GitHub repository ``root``'s ``origin`` points at."""
    remote = await run_git(backend, root, "config --get remote.origin.url")
    url = normalize_remote(first_line(remote.output)) if remote.ok else ""
    match = re.fullmatch(r"https://github\.com/([^/\s]+)/([^/\s]+)", url)
    return f"{match.group(1)}/{match.group(2)}" if match else None
