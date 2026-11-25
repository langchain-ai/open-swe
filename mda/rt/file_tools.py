"""The run's file and shell tools, with the checks around them.

They run in the thread's sandbox, which MDA owns. Reads of the run's skills are
answered from the skills this runtime keeps (``rt.skills``).

Around every call: a command that may push or open a pull request is first put to
the backend's guards, which inspect the push and may need a person's approval, so
the match here is wider than what they block. A command GitHub refused the sandbox's
token for gets the sandbox a fresh one. Tool errors become results the model
can act on. Where the catalog lists the checks, a file posing as an image is not
attached and reading a file loads the ``AGENTS.md`` files above it. The transcript
hears of each call alongside it, and a call that may change a checkout counts
toward the turn's git evidence (``rt.git_evidence``). Code mode calls them through
``code_tools``, so its calls meet the same checks.
"""

import asyncio
import json
import logging
import posixpath
import random
import re
import shlex
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from functools import partial, wraps
from time import monotonic
from typing import Final

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith import get_current_run_tree
from langsmith.sandbox import SandboxRetryableConnectionError
from pydantic import JsonValue, TypeAdapter, ValidationError
from runtime_api.catalog import FilesSpec
from runtime_api.endpoint import call
from runtime_api.operations import CHECK_SHELL, ShellCheck

from rt.auto_review import PATH_ARGUMENTS
from rt.backend import OpenSweBackend, namespace
from rt.git_evidence import OBSERVED_TOOLS, turn_git
from rt.memory_policy import MemoryPolicy
from rt.middleware import reported
from rt.prompts import prompt
from rt.sandbox_setup import sandbox_setup
from rt.skills import SKILL_ROUTES
from rt.subdir_agents import SubdirAgentsReadMiddleware
from rt.tool_errors import ToolErrorMiddleware
from rt.tools import run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY
from rt.validate_image_reads import ValidateImageReadsMiddleware

logger = logging.getLogger(__name__)

# deepagents' file and shell tools.
FILE_TOOLS: Final = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)
# A command the push and pull request guards may stop: one naming a push, the GitHub
# CLI or the pulls API, or nesting shells as deep as the pull request guard reads.
_GUARDED_WORDS: Final = re.compile(r"\b(?:push|gh|pulls)\b")
_SHELL: Final = re.compile(r"\b(?:ba|da|z)?sh\b")
_GUARDED_SHELLS: Final = 3
# The guards read a command as the shell splits it, with its quotes and escapes gone,
# and the shell joins a line it continues.
_QUOTING: Final = re.compile(r"\\\n|[\"'\\]")
# As the backend's sandbox: the client gives up on a command the sandbox never
# answers 30 seconds past its own timeout.
_EXECUTE_GRACE_SECONDS: Final = 30
# Tolerance when attributing an `exit_code == -1` command result to the sandbox timeout.
_TIMEOUT_SLACK_SECONDS: Final = 5
# Matches deepagents' non-strict `ExecuteSchema.timeout: int | None` coercion (e.g. `60.0` -> `60`).
_EXECUTE_TIMEOUT: Final = TypeAdapter(int)
# The longest an ``execute`` may hold the turn; longer work goes to ``background_execute``.
_FOREGROUND_SECONDS: Final = 600
# A foreground command that starts by sleeping this long or longer is waiting on something
# that can wake the thread instead.
_SLEEP_SECONDS: Final = 60
_SLEEP_OPERAND: Final = re.compile(r"(\d+(?:\.\d*)?|\.\d+)([smhd]?)(?=\s|[;&|#]|$)")
_SLEEP_UNITS: Final = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# A leading `cd <dir> &&` or `cd <dir>;` changes nothing about how long the sleep holds the turn.
_LEADING_SLEEP: Final = re.compile(r"\s*(?:cd[ \t]+[^\s;&|]+[ \t]*(?:&&|;)[ \t]*)?sleep[ \t]+")
# Where `background_execute` keeps each job's output, and the shell that waits on it: a job
# already wakes the thread when it ends, so waiting on its output in the foreground only holds
# the turn, the same as a leading sleep.
_BACKGROUND_TASKS: Final = "/tmp/open-swe-background-tasks/"
# A redirection, from its descriptor through its target, which a command never sees as
# arguments; quoted words and the `&&` and `||` operators are matched only to keep them.
_REDIRECTION: Final = re.compile(
    r"""('[^']*'|"(?:[^"\\]|\\.)*"|&&|\|\|)"""
    r"""|(?:(?<![^\s;&|()])\d*(?:>>?|<<?|>&|<&|>\||<>)|&>>?)[ \t]*"""
    r"""(?:'[^']*'|"(?:[^"\\]|\\.)*"|[^\s;&|()<>]+)"""
)
# A shell's options that take the next word as their operand.
_SHELL_OPERANDS: Final = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
# A sandbox call rejected before it started cannot have run, so it is sent again.
_TRANSIENT_BACKOFF_SECONDS: Final = (0.5, 1.0, 2.0)

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


class FileToolsMiddleware(AgentMiddleware):
    """Run the file and shell tools where the thread's files are, with their checks.

    Where they are, and which checks apply, is the run's: the graph is shared by every
    run, so each call reads them from the run in progress.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(
        self,
        backend: OpenSweBackend,
        execute_timeout: int,
    ) -> None:
        self._backend = backend
        # The sandbox's own tools, as the model is offered them; code mode runs them from here.
        self._sandbox_tools: dict[str, BaseTool] = {}
        self._execute_timeout = execute_timeout
        self._tool_errors = ToolErrorMiddleware(backend)
        self._image_reads = ValidateImageReadsMiddleware()
        self._agents_md = SubdirAgentsReadMiddleware()

    @property
    def _spec(self) -> FilesSpec:
        return self._backend.run.catalog.files

    @property
    def _shell_search(self) -> bool:
        """Whether the run searches through ``execute``, its catalog keeping ``grep`` and ``glob`` out."""
        return {"grep", "glob"} <= {*self._backend.run.catalog.excluded}

    @property
    def _background_execute(self) -> bool:
        """Whether this call offers ``background_execute`` for long-running commands."""
        if namespace():
            return False
        catalog = self._backend.run.catalog
        unavailable = {*catalog.excluded, *catalog.disabled}
        return "background_execute" not in unavailable and any(
            spec.name == "background_execute" for spec in catalog.operations
        )

    def _described_tool(self, tool: BaseTool) -> BaseTool:
        if tool.name != "execute":
            return tool
        description = tool.description
        if self._shell_search:
            description = description.replace(
                "- You MUST avoid using search commands like find and grep. Instead use the grep, glob tools to search. Use read_file rather than cat/head/tail.\n"
                "    - execute(command=\"find . -name '*.py'\")  # Use glob tool instead\n"
                "    - execute(command=\"grep -r 'pattern' .\")  # Use grep tool instead",
                "- Search repository contents through execute with `git grep -n`, which reads only "
                "tracked files and skips dependency trees; list file names with `git ls-files`. "
                "Use `grep -rn` only on specific source directories, never on the repository root "
                "or a directory holding `node_modules`, `.venv` or other installed dependencies: "
                "it walks every untracked file there (`--include` and `| grep -v` do not prevent "
                "the walk) and can take minutes. There are no separate grep/glob tools. "
                "Follow repository-specific search instructions, including restrictions on ripgrep. "
                "Use read_file rather than cat/head/tail.",
            )
        if self._background_execute:
            description = description.replace(
                "use the optional timeout to override the default.",
                f"default timeout is {self._execute_timeout}s (max {_FOREGROUND_SECONDS}s). "
                "For builds, test suites, or commands expected to run longer than ~1-2 minutes, "
                "use `background_execute` instead of blocking in `execute`.",
            )
        if description == tool.description:
            return tool
        return tool.model_copy(update={"description": description})

    def code_tools(self, names: Iterable[str]) -> list[StructuredTool]:
        """The file and shell tools among ``names``, for code mode, each call checked."""
        # Only their names, descriptions and schemas: each call runs the sandbox's own tool.
        templates = {tool.name: tool for tool in FilesystemMiddleware().tools}
        return [
            self._code_tool(self._described_tool(templates[name]))
            for name in sorted({*names} & {*templates})
        ]

    def _code_tool(self, template: BaseTool) -> StructuredTool:
        name = template.name

        async def run(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
            call_ = ToolCall(name=name, args=arguments, id=runtime.tool_call_id, type="tool_call")
            request = ToolCallRequest(
                tool_call=call_, tool=template, state=runtime.state, runtime=runtime
            )
            return await self.awrap_tool_call(request, self._run_own)

        return StructuredTool.from_function(
            coroutine=run,
            name=name,
            description=template.description,
            args_schema=convert_to_openai_tool(template)["function"]["parameters"],
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if self._shell_search or self._background_execute:
            request = request.override(
                tools=[
                    self._described_tool(tool) if isinstance(tool, BaseTool) else tool
                    for tool in request.tools
                ]
            )
        self._sandbox_tools.update(
            (tool.name, tool)
            for tool in request.tools
            if isinstance(tool, BaseTool) and tool.name in FILE_TOOLS
        )
        # What the last calls changed is fingerprinted while the model works.
        turn_git(self._backend.run).settle()
        return await handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        call_ = request.tool_call
        if call_["name"] not in FILE_TOOLS:
            return await handler(request)
        if request.tool is not None:
            request = request.override(tool=self._skill_tool(request, request.tool))
        if _under(("/memories/", *SKILL_ROUTES), call_["name"], call_["args"]):
            run: Handler = handler
        else:
            # Capture before `reported` pushes this tool call's id onto `namespace()`.
            background_execute = self._background_execute
            run = partial(
                self._observed,
                handler=partial(
                    _retried,
                    handler=partial(
                        self._within_deadline,
                        handler=handler,
                        background_execute=background_execute,
                    ),
                ),
            )
        # Any call may reach memory, a search from ``/`` included, so each meets its policy.
        run = partial(self._in_memory, handler=run)
        # The transcript records a call the guards refuse, with their reason, as any other.
        return await reported(
            self._backend,
            request,
            partial(self._checked, handler=partial(self._screened_run, handler=run)),
        )

    async def _screened_run(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        args = request.tool_call["args"]
        timeout = self._parsed_timeout(args.get("timeout"))
        command = args.get("command")
        refusal = None
        if request.tool_call["name"] == "execute":
            # 0 turns a command's timeout off, so it is past the limit too.
            if timeout is not None and not 0 < timeout <= _FOREGROUND_SECONDS:
                refusal = prompt(
                    "tools/foreground-limit", timeout=timeout, limit=_FOREGROUND_SECONDS
                )
            elif isinstance(command, str) and (duration := self._foreground_sleep(command)):
                refusal = prompt("tools/foreground-sleep", duration=duration)
            elif (
                isinstance(command, str)
                and _BACKGROUND_TASKS in command
                and self._foreground_poll(command)
            ):
                refusal = prompt("tools/foreground-poll")
        if refusal is not None:
            return ToolMessage(
                content=refusal,
                tool_call_id=request.tool_call.get("id") or "",
                name="execute",
                status="error",
            )
        screened = await self._screened(request)
        if isinstance(screened, ToolMessage):
            return screened
        return await handler(screened)

    @classmethod
    def _foreground_poll(cls, command: str, depth: int = 0) -> bool:
        if depth > _GUARDED_SHELLS:
            return False
        # The `&` of a redirection such as `2>&1` or `&>file` sends nothing to the background.
        unredirected = _REDIRECTION.sub(lambda match: match[1] or " ", command)
        shell = shlex.shlex(unredirected, posix=True, punctuation_chars=";&|()\n")
        shell.whitespace = " \t\r"
        shell.whitespace_split = True
        # Each simple command, and the operator after it, newlines aside.
        commands: list[tuple[list[str], str]] = [([], "")]
        try:
            for token in shell:
                if token and all(char in ";&|()\n" for char in token):
                    commands[-1] = (commands[-1][0], token.replace("\n", ""))
                    commands.append(([], ""))
                else:
                    commands[-1][0].append(token)
        except ValueError:
            logger.debug("Could not screen foreground poll", exc_info=True)
            return False
        # A pipeline runs in the background as a whole when a trailing `&` sends it there.
        backgrounded = [operator == "&" for _, operator in commands]
        for index in range(len(commands) - 2, -1, -1):
            if commands[index][1] in {"|", "|&"}:
                backgrounded[index] = backgrounded[index + 1]
        loops = 0
        for (words, _), in_background in zip(commands, backgrounded, strict=True):
            while words and words[0] in {"do", "then", "else", "done"}:
                if words.pop(0) == "done":
                    loops = max(0, loops - 1)
            if not words:
                continue
            if words[0] in {"for", "while", "until"}:
                loop = words[0]
                loops += 1
                words = words[1:]
                condition = next((word for word in words if not re.match(r"\w+=", word)), "")
                if loop in {"while", "until"} and condition != "read":
                    return True
            if words and words[0] == "timeout":
                # Past its options, such as `-k 5`, to the duration and the command it runs.
                index = 1
                while index < len(words) and words[index].startswith("-"):
                    index += 2 if words[index] in {"-k", "-s", "--kill-after", "--signal"} else 1
                words = words[index + 1 :]
            if not words:
                continue
            executable, *arguments = words
            executable = posixpath.basename(executable)
            if executable in {"sh", "bash", "dash", "zsh"} and (
                script := cls._shell_script(arguments)
            ):
                if cls._foreground_poll(script, depth + 1):
                    return True
            if executable == "watch" or (loops and executable == "sleep"):
                return True
            if executable == "sleep" and not in_background:
                # A long pause between reads of the job's output holds the turn as a loop does.
                seconds = 0.0
                for operand in arguments:
                    if not (match := _SLEEP_OPERAND.fullmatch(operand)):
                        break
                    seconds += float(match[1]) * _SLEEP_UNITS[match[2]]
                if seconds >= _SLEEP_SECONDS:
                    return True
            if executable == "tail":
                options = iter(arguments)
                for option in options:
                    if option == "--":
                        break
                    if option in {"-n", "-c", "--lines", "--bytes", "--pid", "--sleep-interval"}:
                        next(options, None)
                    elif option == "--follow" or option.startswith("--follow="):
                        return True
                    elif re.fullmatch(r"-[a-zA-Z]*[fF][a-zA-Z]*", option):
                        return True
        return False

    @staticmethod
    def _shell_script(arguments: list[str]) -> str | None:
        """The script a shell runs with `-c`, past its other options and `--`."""
        command = False
        index = 0
        while index < len(arguments):
            word = arguments[index]
            if word == "--":
                index += 1
                break
            if word in _SHELL_OPERANDS:
                index += 2
                continue
            if len(word) < 2 or word[0] not in "-+":
                break
            command = command or (word[0] == "-" and word[1] != "-" and "c" in word[1:])
            index += 1
        return arguments[index] if command and index < len(arguments) else None

    @staticmethod
    def _foreground_sleep(command: str) -> str | None:
        sleep = _LEADING_SLEEP.match(command)
        if sleep is None:
            return None
        remaining = command[sleep.end() :]
        operands: list[str] = []
        seconds = 0.0
        while operand := _SLEEP_OPERAND.match(remaining):
            operands.append(operand[0])
            seconds += float(operand[1]) * _SLEEP_UNITS[operand[2]]
            remaining = remaining[operand.end() :].lstrip(" \t")
        if seconds < _SLEEP_SECONDS:
            return None
        shell = shlex.shlex(remaining, posix=False, punctuation_chars=";&|<>\n")
        shell.whitespace = " \t\r"
        shell.whitespace_split = True
        shell.commenters = ""
        try:
            for token in shell:
                if re.search(r"""[\\'"`$(){}]|&&|\|\|""", token):
                    break
                if token.rstrip("\n") == "&":
                    return None
                if token.startswith((";", "\n", "#")):
                    break
        except ValueError:
            logger.debug("Could not screen foreground sleep", exc_info=True)
        return " ".join(operands)

    async def _screened(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        """The request or an approved push as the guards fixed it, otherwise their refusal."""
        command = request.tool_call["args"].get("command")
        if (
            request.tool_call["name"] != "execute"
            or "shell_commands" not in self._spec.guards
            or not isinstance(command, str)
            or not _guarded(command)
        ):
            return request
        verdict = await call(
            self._backend, CHECK_SHELL, ShellCheck(run_id=run_id(), tool="execute", command=command)
        )
        if not verdict.allowed:
            return ToolMessage(
                content=verdict.refusal or json.dumps({"status": "error", "error": "refused"}),
                tool_call_id=request.tool_call.get("id") or "",
                name="execute",
                status="error",
            )
        if verdict.command is None:
            return request
        arguments = {**request.tool_call["args"], "command": verdict.command}
        return request.override(tool_call={**request.tool_call, "args": arguments})

    async def _observed(self, request: ToolCallRequest, handler: Handler) -> ToolMessage | Command:
        """``handler`` once the sandbox has booted; when the call may change a checkout, once the
        sandbox is ready and the turn's starting fingerprint taken."""
        setup = sandbox_setup(self._backend.run)
        if request.tool_call["name"] not in OBSERVED_TOOLS:
            await setup.booted()
            return await handler(request)
        await setup.done()
        git = turn_git(self._backend.run)
        await git.changing()
        started = time.time_ns()
        try:
            return await handler(request)
        finally:
            git.changed(started)

    def _skill_tool(self, request: ToolCallRequest, tool: BaseTool) -> BaseTool:
        path = _absolute(request.tool_call["args"].get("file_path"))
        if request.tool_call["name"] != "read_file" or path is None:
            return tool
        parent, filename = posixpath.split(path)
        source, skill = posixpath.split(parent)
        listed = any(
            source == prefix.rstrip("/")
            for prefix in (
                *SKILL_ROUTES,
                *self._backend.run.catalog.skill_sources,
            )
        )
        if (
            filename != "SKILL.md"
            or not skill
            or not (listed or source.endswith(("/.agents/skills", "/.claude/skills")))
        ):
            return tool
        if not isinstance(tool, StructuredTool) or tool.coroutine is None:
            return tool
        read = tool.coroutine

        @wraps(read)
        async def read_skill(*args: object, **kwargs: object) -> object:
            result = await read(*args, **kwargs)
            failed = (isinstance(result, ToolMessage) and result.status == "error") or (
                isinstance(result, str) and result.startswith("Error:")
            )
            if (
                not failed
                and (span := get_current_run_tree()) is not None
                and span.run_type == "tool"
                and span.name == "read_file"
            ):
                span.add_metadata({"ls_skill_name": skill})
            return result

        return tool.model_copy(update={"coroutine": read_skill})

    async def _in_memory(self, request: ToolCallRequest, handler: Handler) -> ToolMessage | Command:
        """``handler``'s call, unless the memory policy refuses it."""
        call_ = request.tool_call
        policy = MemoryPolicy(self._backend.run.catalog.memory)
        if (refusal := policy.refusal(call_["name"], call_["args"])) is None:
            return await handler(request)
        return ToolMessage(
            content=refusal, tool_call_id=call_["id"] or "", name=call_["name"], status="error"
        )

    async def _run_own(self, request: ToolCallRequest) -> ToolMessage | Command:
        """A code mode call, run by the tool the model's own call of it runs."""
        tool = self._sandbox_tools[request.tool_call["name"]]
        return await _invoked(self._skill_tool(request, tool), request)

    async def _checked(self, request: ToolCallRequest, handler: Handler) -> ToolMessage | Command:
        """``handler`` in the graph's own order: image reads, then tool errors, then AGENTS.md."""
        checks = self._spec.checks
        checked = handler
        for check in (
            self._agents_md if "agents_md" in checks else None,
            self._tool_errors,
            self._image_reads if "image_reads" in checks else None,
        ):
            if check is not None:
                checked = partial(check.awrap_tool_call, handler=checked)
        return await checked(request)

    @staticmethod
    def _parsed_timeout(value: object) -> int | None:
        """Parse `timeout` the same way deepagents' `ExecuteSchema` validates it."""
        if value is None:
            return None
        try:
            return _EXECUTE_TIMEOUT.validate_python(value)
        except ValidationError:
            return None

    async def _within_deadline(
        self,
        request: ToolCallRequest,
        handler: Handler,
        *,
        background_execute: bool | None = None,
    ) -> ToolMessage | Command:
        """Give up on a command the sandbox never answers, and explain one killed at its timeout."""
        timeout = self._parsed_timeout(request.tool_call["args"].get("timeout"))
        seconds = timeout if timeout is not None else self._execute_timeout
        if request.tool_call["name"] != "execute" or seconds <= 0:
            return await handler(request)
        deadline = seconds + _EXECUTE_GRACE_SECONDS
        started = monotonic()
        try:
            result = await asyncio.wait_for(handler(request), deadline)
        except TimeoutError:
            logger.warning("The sandbox never answered a command", extra={"deadline": deadline})
            return ToolMessage(
                content=f"Command timed out after {deadline}s; the sandbox never answered.",
                tool_call_id=request.tool_call.get("id") or "",
                name=request.tool_call["name"],
                status="error",
            )
        return self._annotate_timeout(
            result,
            seconds,
            monotonic() - started,
            background_execute=self._background_execute
            if background_execute is None
            else background_execute,
        )

    def _annotate_timeout(
        self,
        result: ToolMessage | Command,
        seconds: int,
        elapsed: float,
        *,
        background_execute: bool,
    ) -> ToolMessage | Command:
        """Explain an `exit_code == -1` result when the command ran to its timeout."""
        if (
            not isinstance(result, ToolMessage)
            or not isinstance(result.content, str)
            or not isinstance(result.artifact, Mapping)
            or result.artifact.get("exit_code") != -1
        ):
            return result
        if elapsed < max(seconds - _TIMEOUT_SLACK_SECONDS, seconds * 0.9):
            return result
        hint = prompt(
            "tools/foreground-timeout",
            timeout=seconds,
            limit=_FOREGROUND_SECONDS,
            background_execute=background_execute,
        )
        content = f"{result.content}\n{hint}" if result.content else hint
        return result.model_copy(update={"content": content})


async def _invoked(tool: BaseTool, request: ToolCallRequest) -> ToolMessage | Command:
    call_id = request.tool_call.get("id") or ""
    result = await tool.ainvoke(
        ToolCall(
            name=tool.name,
            args={**request.tool_call["args"], "runtime": request.runtime},
            id=call_id,
            type="tool_call",
        )
    )
    if isinstance(result, ToolMessage | Command):
        return result
    return ToolMessage(content=str(result), tool_call_id=call_id, name=tool.name)


def _under(prefixes: Iterable[str], name: str, arguments: Mapping[str, object]) -> bool:
    """Whether the path ``name``'s call acts on is under one of ``prefixes``."""
    path = _absolute(arguments.get(PATH_ARGUMENTS.get(name, "")))
    return path is not None and any(
        path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes
    )


def _guarded(command: str) -> bool:
    unquoted = _QUOTING.sub("", command)
    return bool(_GUARDED_WORDS.search(unquoted)) or len(_SHELL.findall(unquoted)) >= _GUARDED_SHELLS


def _absolute(path: object) -> str | None:
    """``path`` as the file tools resolve it, rooted at ``/``."""
    if not isinstance(path, str) or not path.strip():
        return None
    return posixpath.normpath("/" + path.strip().lstrip("/"))


async def _retried(request: ToolCallRequest, handler: Handler) -> ToolMessage | Command:
    """Send a call the sandbox refused before it started again, as the backend's sandbox does."""
    for backoff in _TRANSIENT_BACKOFF_SECONDS:
        try:
            return await handler(request)
        except SandboxRetryableConnectionError:
            logger.warning("The sandbox refused a tool call before it started; retrying")
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
    return await handler(request)
