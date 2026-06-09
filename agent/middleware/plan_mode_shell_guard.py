"""Tool-layer read-only enforcement for the shell tool during plan mode.

Prompt instructions are not a security control: the `execute` tool runs
arbitrary shell commands inside the sandbox, whose GitHub proxy can push to
github.com. Adversarial content read during planning (repo files, PR bodies,
web pages) could prompt-inject the model into running `git push`, `gh pr
create`, package installs, or file writes, bypassing the read-only guarantee.

This middleware intercepts shell tool calls while plan mode is active and blocks
anything that is not on a conservative read-only allowlist, enforcing the
guarantee at the tool layer regardless of model instruction compliance. The
parser fails safe: unparseable input or unrecognized command words are blocked.
"""

from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

# Shell tool names that run arbitrary commands in the sandbox.
SHELL_TOOL_NAMES: frozenset[str] = frozenset({"execute", "bash", "shell", "run_terminal_cmd"})

# Base commands that only read state. Anything not here is blocked in plan mode.
READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "file",
        "stat",
        "tree",
        "find",
        "wc",
        "du",
        "df",
        "readlink",
        "realpath",
        "basename",
        "dirname",
        "echo",
        "printf",
        "printenv",
        "whoami",
        "hostname",
        "uname",
        "date",
        "which",
        "type",
        "true",
        "false",
        "test",
        "grep",
        "rg",
        "egrep",
        "fgrep",
        "cut",
        "tr",
        "diff",
        "comm",
        "column",
        "jq",
        "nl",
        "tac",
        "fold",
        "cmp",
        "sort",
        "uniq",
        "paste",
        "seq",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "xxd",
        "od",
        "strings",
        "git",
    }
)

# git subcommands that mutate local/remote state or the working tree. Anything
# not listed (clone, fetch, log, status, diff, show, blame, grep, ls-files, ...)
# is read-only and allowed.
GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "push",
        "commit",
        "am",
        "apply",
        "cherry-pick",
        "revert",
        "rebase",
        "merge",
        "reset",
        "checkout",
        "switch",
        "restore",
        "add",
        "rm",
        "mv",
        "clean",
        "stash",
        "init",
        "gc",
        "prune",
        "fsck",
        "repack",
        "worktree",
        "submodule",
        "format-patch",
        "send-email",
        "request-pull",
        "update-ref",
        "update-index",
        "symbolic-ref",
        "filter-branch",
        "replace",
        "notes",
        "bundle",
        "tag",
        "branch",
        "remote",
        "config",
        "gui",
        "citool",
    }
)

# git global options (before the subcommand) that consume the following token as
# their value, e.g. `git -C <path> status`. Their values must be skipped so the
# real subcommand is identified correctly.
_GIT_VALUE_OPTIONS: frozenset[str] = frozenset(
    {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
)

# git global options that can run arbitrary commands or otherwise subvert the
# read-only guarantee regardless of the subcommand (e.g. `-c alias.x='!sh'`).
_GIT_BLOCKED_OPTIONS: frozenset[str] = frozenset({"-c", "--config-env", "--exec-path"})


def _git_subcommand(rest: list[str]) -> str | None:
    """Return the git subcommand, skipping global options; raise on blocked ones."""
    index = 0
    while index < len(rest):
        token = rest[index]
        if not token.startswith("-"):
            return token
        name = token.split("=", 1)[0]
        if name in _GIT_BLOCKED_OPTIONS:
            raise PlanModeBlockedError(f"`git {name}` is not allowed in plan mode")
        if name in _GIT_VALUE_OPTIONS and "=" not in token:
            index += 1  # skip the option's value argument
        index += 1
    return None


# find predicates that execute commands or write files.
_FIND_WRITE_PREDICATES: frozenset[str] = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

_SUBSTITUTION_TOKENS = ("$(", "`", "<(", ">(", "${")
_REDIRECT_RE = re.compile(r"[<>]")


class PlanModeBlockedError(Exception):
    """A shell command was rejected by the plan-mode read-only guard."""


def _check_segment(tokens: list[str]) -> None:
    index = 0
    while index < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]):
        index += 1  # skip leading VAR=value assignments
    if index >= len(tokens):
        return
    base = tokens[index].rsplit("/", 1)[-1]
    if base not in READ_ONLY_COMMANDS:
        raise PlanModeBlockedError(f"`{base}` is not an allowed read-only command in plan mode")
    rest = tokens[index + 1 :]
    if base == "git":
        sub = _git_subcommand(rest)
        if sub and sub in GIT_WRITE_SUBCOMMANDS:
            raise PlanModeBlockedError(f"`git {sub}` changes state and is not allowed in plan mode")
    if base == "find":
        bad = next((t for t in rest if t in _FIND_WRITE_PREDICATES), None)
        if bad:
            raise PlanModeBlockedError(
                f"`find {bad}` executes or writes and is not allowed in plan mode"
            )


def assert_read_only(command: str) -> None:
    """Raise PlanModeBlockedError if *command* is not a read-only shell command."""
    if any(marker in command for marker in _SUBSTITUTION_TOKENS):
        raise PlanModeBlockedError("command/variable substitution is not allowed in plan mode")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError as exc:
        raise PlanModeBlockedError(f"could not parse shell command: {exc}") from exc

    segment: list[str] = []
    for token in tokens:
        if _REDIRECT_RE.search(token):
            raise PlanModeBlockedError("I/O redirection is not allowed in plan mode")
        if set(token) <= {"&", "|", ";", "(", ")"}:
            _check_segment(segment)
            segment = []
            continue
        segment.append(token)
    _check_segment(segment)


class PlanModeShellGuardMiddleware(AgentMiddleware):
    """Block non read-only shell commands while plan mode is active.

    Rejected commands return an error ToolMessage so the model can adjust and
    keep planning instead of crashing the run.
    """

    state_schema = AgentState

    def _guard(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        if not isinstance(tool_call, dict) or tool_call.get("name") not in SHELL_TOOL_NAMES:
            return None
        args = tool_call.get("args") or {}
        command = args.get("command") if isinstance(args, dict) else None
        if not isinstance(command, str) or not command.strip():
            return None
        try:
            assert_read_only(command)
        except PlanModeBlockedError as exc:
            logger.info("Plan mode blocked shell command: %s", exc)
            return ToolMessage(
                content=(
                    f"Blocked by plan mode (read-only): {exc}. "
                    "Plan mode only permits read-only research commands. Use read-only "
                    "tools to investigate and present your implementation plan instead."
                ),
                tool_call_id=tool_call.get("id"),
                status="error",
            )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        blocked = self._guard(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        blocked = self._guard(request)
        return blocked if blocked is not None else await handler(request)
