"""Auto mode for a thread run on a person's own machine: each file and shell call is let
through, put to Jev (``runtime_api.classifier``), or refused before it runs.

A bridged thread's tools run unconfined on the person's computer (``bridge-client``), so
a call that leaves the checkout, touches credentials or reaches beyond the project is
reviewed first. The classifier reads what the person asked for and the calls the run
made this turn, never tool results or the model's prose, so text a tool returned cannot
argue it into allowing anything. It fails closed: no confident answer is a refusal, and
the run carries on without the call. A call the person asked for in their own words is
allowed.
"""

import json
import logging
import posixpath
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.messages import AIMessage, HumanMessage
from langchain_typesafe import Choice
from pydantic import JsonValue
from runtime_api.classifier import DEFAULT_JEV, JevDecision
from runtime_api.provenance import Provenance

from rt.backend import STATE_KEY, Run, RunLocal
from rt.input_messages import input_message_text
from rt.prompts import prompt
from rt.untrusted import OPEN_TAG

logger = logging.getLogger(__name__)

# The argument naming the path each file tool acts on.
PATH_ARGUMENTS: Final = {
    "ls": "path",
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "glob": "path",
    "grep": "path",
}
_READS: Final = frozenset({"ls", "read_file", "glob", "grep"})
# The arguments of each tool that run as shell on the machine.
_SHELL_ARGUMENTS: Final = {
    "execute": ("command",),
    "background_execute": ("command",),
    "monitor": ("command", "filter"),
}
REVIEWED: Final = frozenset({*PATH_ARGUMENTS, *_SHELL_ARGUMENTS})

Risk = Literal[
    "routine",
    "exfiltration",
    "credential_access",
    "destructive",
    "persistence",
    "security_bypass",
    "scope_escalation",
]
RISKS: Final[tuple[Risk, ...]] = (
    "routine",
    "exfiltration",
    "credential_access",
    "destructive",
    "persistence",
    "security_bypass",
    "scope_escalation",
)
AUTHORIZATIONS: Final = ("explicit", "none")
# Above Jev's own acceptance floor: allowing takes more certainty than refusing.
ALLOW_CONFIDENCE: Final = 0.85
# As Claude Code's auto mode: past these, the model is told to stop and ask the person.
CONSECUTIVE_REFUSALS: Final = 3
TOTAL_REFUSALS: Final = 20

# Directories and files that hold credentials; a path through one is reviewed. Compared
# casefolded: a Mac's filesystem ignores case, so ``~/.SSH`` is ``~/.ssh``.
_SECRET_PARTS: Final = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".docker",
        ".password-store",
        ".1password",
        "gcloud",
        "keychains",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        ".git-credentials",
        ".zsh_history",
        ".bash_history",
    }
)
# A ``grep`` file filter that runs unreviewed: files of one extension, optionally under
# plain directories. Any other filter (a dotfile, braces, brackets, a bare ``*``) may
# reach a secret, as may the extensions keys and environment files carry.
_GREP_FILTER: Final = re.compile(r"(?:[\w.-]+/|\*\*/)*\*\.([a-z0-9]{1,10})")
_SECRET_EXTENSIONS: Final = frozenset(
    {
        "env",
        "local",
        "production",
        "development",
        "staging",
        "test",
        "pem",
        "key",
        "p12",
        "pfx",
        "crt",
        "cer",
        "der",
        "jks",
        "keystore",
        "kdbx",
        "tfvars",
        "tfstate",
        "netrc",
        "npmrc",
        "pypirc",
        "pgpass",
    }
)
# What a write may not touch even inside the checkout: git's own internals and hooks, CI
# workflows that run with the repository's secrets, and environment files.
_PROTECTED_WRITE_PARTS: Final = frozenset({".git", ".husky", ".envrc"})
_PROTECTED_WRITE_PREFIXES: Final = (".github/workflows/",)
_SYSTEM_PREFIXES: Final = ("/etc/", "/private/etc/", "/library/", "/system/")
# Shell syntax a fixed read-only command may not contain: chaining, pipes, redirection,
# substitution, variables, globs, braces and home expansion, any of which makes what it
# reads something other than the operands as written.
_SHELL_SYNTAX: Final = re.compile(r"[\n\r;&|`<>*?\[\]{}~]|\$")
_READ_ONLY_COMMANDS: Final = frozenset({"ls", "pwd", "cat", "head", "tail", "wc", "file", "stat"})
_READ_ONLY_GIT: Final = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files"})
# Git options that write a file or run a configured program.
_GIT_SIDE_EFFECTS: Final = re.compile(r"^--(?:output|ext-diff)")
# Never run on the person's machine in auto mode, whatever the person said.
_FORBIDDEN: Final = (
    ("sudo", re.compile(r"(?:^|[\s;&|(`])sudo\b")),
    (
        "keychain",
        re.compile(r"\bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain|export)\b"),
    ),
    ("remote_script", re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")),
)
# The classifier reads at most this much: the end of each request, and of each argument.
_REQUEST_CHARS: Final = 1_500
_REQUESTS: Final = 3
_ARGUMENT_CHARS: Final = 500
_EARLIER_CALLS: Final = 12
# The classifier reads the shell of the call under review whole; a longer one is refused.
_COMMAND_CHARS: Final = 10_000
# Prints where each path in its ``for`` list really is on the machine, one line each:
# links followed, a path that does not exist yet resolved through its nearest existing
# parent, and ``?`` for a parent it cannot enter or a link it gave up following.
_RESOLVE: Final = (
    'do n=0; while [ -L "$p" ] && [ $n -lt 40 ] && t=$(readlink -- "$p"); '
    "do case $t in /*) p=$t;; *) p=${p%/*}/$t;; esac; n=$((n+1)); done; "
    's=; while [ "$p" != / ] && [ ! -e "$p" ] && [ ! -L "$p" ]; '
    'do s="/${p##*/}$s"; p=${p%/*}; p=${p:-/}; done; '
    'if [ -d "$p" ]; then d=$(cd -P -- "$p" 2>/dev/null && pwd -P) '
    "&& printf '%s%s\\n' \"${d%/}\" \"$s\" || echo '?'; "
    "elif [ -L \"$p\" ]; then echo '?'; "
    'else q=${p%/*}; q=${q:-/}; d=$(cd -P -- "$q" 2>/dev/null && pwd -P) '
    '&& printf \'%s/%s%s\\n\' "${d%/}" "${p##*/}" "$s" || echo \'?\'; fi; done'
)
_RESOLVE_SECONDS: Final = 15

Disposition = Literal["allow", "review", "forbid"]


@dataclass(frozen=True)
class Review:
    """What auto mode made of one call: whether it runs, and why not."""

    allowed: bool
    # The category behind a refusal: a risk, a forbidden kind of command, or ``unsure``.
    category: str = ""
    # Whether nothing the person says lets it run.
    forbidden: bool = False


@dataclass(frozen=True)
class Action:
    """A call auto mode is asked about, with where the run's checkout is."""

    name: str
    arguments: Mapping[str, JsonValue]
    # The checkout on the person's machine; None when the run has not said.
    work_dir: str | None
    # Where on the machine a write may go besides the checkout: the run's artifacts.
    scratch: Sequence[str] = ()
    # Paths the backend serves rather than the machine (offloads, skills), which a call
    # may reach too.
    served: Sequence[str] = ()

    def disposition(self) -> tuple[Disposition, str]:
        """Whether this call runs unreviewed, goes to the classifier, or never runs; with
        the reason a forbidden call is refused. An unreviewed call still has its paths
        confirmed on the machine (``targets``), where a symlink may lead elsewhere."""
        for command in self._commands():
            for category, pattern in _FORBIDDEN:
                if pattern.search(command):
                    return "forbid", category
        targets = self.targets()
        if targets is not None and self.permits(targets):
            return "allow", ""
        return "review", ""

    def targets(self) -> list[str] | None:
        """The paths on the machine the call would run unreviewed on, as written; None
        when it cannot run unreviewed whatever they are."""
        if self.name in _SHELL_ARGUMENTS:
            commands = self._commands()
            if not commands:
                return None
            targets: list[str] = []
            for command in commands:
                operands = self._operands(command)
                if operands is None:
                    return None
                for operand in operands:
                    if (path := self._shell_path(operand)) is None:
                        return None
                    targets.append(path)
            return targets
        path = self._file_path()
        return None if path is None else [path]

    def permits(self, targets: Sequence[str]) -> bool:
        """Whether the call may touch ``targets`` unreviewed, its roots as they stand."""
        if self.name in _SHELL_ARGUMENTS:
            return self.work_dir is not None and all(
                self._inside(path, self.work_dir) and not self._secret(path) for path in targets
            )
        if any(self._secret(path) for path in targets):
            return False
        if self.name == "grep" and not self._grep_skips_secrets():
            return False
        if self.name in _READS:
            return all(self._readable(path) for path in targets)
        return all(self._writable(path) for path in targets)

    def on_machine(self, targets: Sequence[str]) -> list[str]:
        """``targets`` that are on the person's machine rather than the backend's."""
        return [
            path for path in targets if not any(self._inside(path, root) for root in self.served)
        ]

    def summary(self, *, full_commands: bool = False) -> dict[str, JsonValue]:
        """The call as the classifier reads it, long arguments cut short; with
        ``full_commands``, what it runs as shell is kept whole, since every character of
        it runs."""
        shell = _SHELL_ARGUMENTS.get(self.name, ()) if full_commands else ()
        return {
            "tool": self.name,
            "arguments": {
                key: value if key in shell else self._clipped(value)
                for key, value in self.arguments.items()
            },
        }

    def too_long(self) -> bool:
        """Whether what the call runs as shell is too long for the classifier to read."""
        return any(len(command) > _COMMAND_CHARS for command in self._commands())

    @classmethod
    def _clipped(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            return value if len(value) <= _ARGUMENT_CHARS else f"{value[:_ARGUMENT_CHARS]}…"
        if isinstance(value, Mapping):
            return {key: cls._clipped(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._clipped(item) for item in value]
        return value

    def _commands(self) -> list[str]:
        return [
            value
            for key in _SHELL_ARGUMENTS.get(self.name, ())
            if isinstance(value := self.arguments.get(key), str) and value.strip()
        ]

    def _file_path(self) -> str | None:
        """The path a file tool acts on, rooted at ``/`` as the file tools root it; the
        checkout when a search names none."""
        raw = self.arguments.get(PATH_ARGUMENTS[self.name])
        if raw is None and self.name in {"glob", "grep", "ls"}:
            return self.work_dir
        if not isinstance(raw, str) or not raw.strip() or raw.strip().startswith("~"):
            return None
        return posixpath.normpath("/" + raw.strip().lstrip("/"))

    def _shell_path(self, token: str) -> str | None:
        """``token`` as the shell, started in the checkout, reads it as a path."""
        if token.startswith("~") or self.work_dir is None:
            return None
        return posixpath.normpath(posixpath.join(self.work_dir, token))

    def _inside(self, path: str, root: str) -> bool:
        path, root = path.casefold(), root.casefold().rstrip("/") or "/"
        return path == root or path.startswith(root if root == "/" else f"{root}/")

    def _secret(self, path: str) -> bool:
        folded = path.casefold()
        parts = folded.split("/")
        # A key or state file by its extension, or git's own directory, whose config
        # may hold a remote's token.
        extension = parts[-1].rpartition(".")[2] if "." in parts[-1] else ""
        return (
            any(
                part in _SECRET_PARTS or part == ".git" or part.startswith((".env", "credentials"))
                for part in parts
            )
            or extension in _SECRET_EXTENSIONS
            or folded.startswith(_SYSTEM_PREFIXES)
            or folded in {"/etc", "/private/etc"}
        )

    def _readable(self, path: str) -> bool:
        roots = (*self.scratch, *self.served, *((self.work_dir,) if self.work_dir else ()))
        return any(self._inside(path, root) for root in roots)

    def _grep_skips_secrets(self) -> bool:
        """Whether ``grep``'s file filter keeps it to files of one ordinary extension;
        without one it searches every file, dotfiles included."""
        pattern = self.arguments.get("glob")
        if not isinstance(pattern, str):
            return False
        match = _GREP_FILTER.fullmatch(pattern.strip().casefold())
        return (
            match is not None
            and match.group(1) not in _SECRET_EXTENSIONS
            and not any(part.startswith(".") for part in pattern.split("/")[:-1])
        )

    def _writable(self, path: str) -> bool:
        if any(self._inside(path, root) for root in (*self.scratch, *self.served)):
            return True
        if self.work_dir is None or not self._inside(path, self.work_dir):
            return False
        relative = path[len(self.work_dir.rstrip("/")) :].lstrip("/").casefold()
        return not (
            any(part in _PROTECTED_WRITE_PARTS for part in relative.split("/"))
            or relative.startswith(_PROTECTED_WRITE_PREFIXES)
        )

    def _operands(self, command: str) -> list[str] | None:
        """The operands of ``command`` when it is one fixed read-only program; None otherwise."""
        if _SHELL_SYNTAX.search(command):
            return None
        try:
            parts = shlex.split(command)
        except ValueError:
            return None
        if not parts:
            return None
        if parts[0] == "git":
            if len(parts) < 2 or parts[1] not in _READ_ONLY_GIT:
                return None
            if any(_GIT_SIDE_EFFECTS.match(part) for part in parts[2:]):
                return None
            arguments, attached = parts[2:], "/"
        elif parts[0] in _READ_ONLY_COMMANDS:
            arguments, attached = parts[1:], "/="
        else:
            return None
        # An option may carry a path, as ``-f/path`` or ``--file=path``, which is not
        # checked as an operand is.
        options = [part for part in arguments if part.startswith("-")]
        if any(char in option for option in options for char in attached):
            return None
        operands = [part for part in arguments if not part.startswith("-")]
        if parts[0] == "git":
            # ``HEAD:.env`` or ``:.env`` names a file git prints, while ``--no-index``
            # reads a path as written, colon and all: check an operand as both.
            return [
                path
                for part in operands
                for path in ((part, part.partition(":")[2]) if ":" in part else (part,))
            ]
        return operands


class AutoReview:
    """One run's auto mode: the classifier's verdicts, and the refusals it has counted."""

    _RUNS: Final[RunLocal["AutoReview"]] = RunLocal(lambda: AutoReview())

    def __init__(self) -> None:
        self.consecutive_refusals = 0
        self.total_refusals = 0

    @classmethod
    async def screen(
        cls,
        run: Run,
        name: str,
        arguments: Mapping[str, JsonValue],
        state: Mapping[str, object],
        machine: SandboxBackendProtocol | None = None,
    ) -> str | None:
        """Why ``run`` may not make this call, as its tool result; None lets it run.

        ``machine`` runs commands where the call would, to confirm where its paths lead;
        without it, no call that names a path runs unreviewed.
        """
        files = run.catalog.files
        if not files.auto_review or name not in REVIEWED:
            return None
        held = state.get(STATE_KEY)
        work_dir = held.get("work_dir") if isinstance(held, Mapping) else None
        action = Action(
            name=name,
            arguments=arguments,
            work_dir=work_dir if isinstance(work_dir, str) and work_dir.startswith("/") else None,
            scratch=(files.artifacts_root,) if files.artifacts_root != "/" else (),
            served=(*files.backend_paths, *run.catalog.skill_sources),
        )
        return await cls._RUNS.get(run).refusal(action, state, machine)

    async def refusal(
        self,
        action: Action,
        state: Mapping[str, object],
        machine: SandboxBackendProtocol | None = None,
    ) -> str | None:
        review = await self.review(action, state, machine)
        if review.allowed:
            self.consecutive_refusals = 0
            return None
        self.consecutive_refusals += 1
        self.total_refusals += 1
        reason = prompt("auto-review/reason", category=review.category)
        if review.forbidden:
            return prompt("auto-review/hard-denied", tool=action.name, reason=reason)
        if (
            self.consecutive_refusals >= CONSECUTIVE_REFUSALS
            or self.total_refusals >= TOTAL_REFUSALS
        ):
            return prompt(
                "auto-review/refused-repeatedly",
                tool=action.name,
                reason=reason,
                count=self.total_refusals,
            )
        return prompt("auto-review/refused", tool=action.name, reason=reason)

    async def review(
        self,
        action: Action,
        state: Mapping[str, object],
        machine: SandboxBackendProtocol | None = None,
    ) -> Review:
        disposition, category = action.disposition()
        if disposition == "allow":
            confirmed = await self.confirmed(action, machine)
            if confirmed:
                return Review(allowed=True)
            # The classifier reads each path as written, never where a link leads, so it
            # cannot judge a call that a link may take out of the checkout.
            if confirmed is False:
                logger.info(
                    "auto_review",
                    extra={
                        "tool": action.name,
                        "outcome": "refuse",
                        "category": "unconfirmed_path",
                    },
                )
                return Review(allowed=False, category="unconfirmed_path")
        if disposition == "forbid":
            logger.info(
                "auto_review",
                extra={"tool": action.name, "outcome": "forbid", "category": category},
            )
            return Review(allowed=False, category=category, forbidden=True)
        if action.too_long():
            logger.info(
                "auto_review",
                extra={"tool": action.name, "outcome": "refuse", "category": "too_long"},
            )
            return Review(allowed=False, category="too_long")
        messages = state.get("messages")
        transcript = messages if isinstance(messages, Sequence) else ()
        risk, authorization = JevDecision(), JevDecision()
        await DEFAULT_JEV.choose(
            self.task(action, transcript),
            questions={
                "risk": Choice(
                    instructions=prompt("auto-review/risk"),
                    criteria={name: prompt(f"auto-review/risks/{name}") for name in RISKS},
                ),
                "authorization": Choice(
                    instructions=prompt("auto-review/authorization"),
                    criteria={
                        name: prompt(f"auto-review/authorizations/{name}")
                        for name in AUTHORIZATIONS
                    },
                ),
            },
            decisions={"risk": risk, "authorization": authorization},
        )
        review = self.verdict(risk, authorization)
        logger.info(
            "auto_review",
            extra={
                "tool": action.name,
                "outcome": "allow" if review.allowed else "refuse",
                "category": review.category,
                "risk": risk.choice,
                "risk_confidence": risk.confidence,
                "risk_outcome": risk.outcome,
                "authorization": authorization.choice,
                "authorization_confidence": authorization.confidence,
            },
        )
        return review

    @staticmethod
    async def confirmed(action: Action, machine: SandboxBackendProtocol | None) -> bool | None:
        """Whether ``action`` still runs unreviewed with its paths and roots resolved on the
        machine: a symlink in the checkout may lead to a key or a startup file. False when
        a path leads out or cannot be resolved; None without a machine to resolve them on."""
        targets = action.on_machine(action.targets() or [])
        if not targets:
            return True
        if machine is None:
            return None
        roots = [root for root in (action.work_dir, *action.scratch) if root is not None]
        paths = [*roots, *targets]
        if any("\n" in path or "\r" in path for path in paths):
            return False
        command = f"for p in {' '.join(shlex.quote(path) for path in paths)}; {_RESOLVE}"
        try:
            answer = await machine.aexecute(command, timeout=_RESOLVE_SECONDS)
        except Exception:
            logger.warning("auto_review could not resolve paths", exc_info=True)
            return False
        lines = answer.output.splitlines()
        if (
            answer.exit_code != 0
            or len(lines) != len(paths)
            or not all(line.startswith("/") for line in lines)
        ):
            return False
        resolved_roots, resolved_targets = lines[: len(roots)], lines[len(roots) :]
        work_dir = resolved_roots.pop(0) if action.work_dir is not None else None
        resolved = replace(action, work_dir=work_dir, scratch=tuple(resolved_roots))
        return resolved.permits(resolved_targets)

    @staticmethod
    def confident(decision: JevDecision) -> bool:
        return (
            decision.outcome == "accepted"
            and decision.confidence is not None
            and decision.confidence >= ALLOW_CONFIDENCE
        )

    @classmethod
    def verdict(cls, risk: JevDecision, authorization: JevDecision) -> Review:
        """Allow a confidently routine call, or one the person confidently authorized;
        refuse the rest, naming the risk when the classifier found one."""
        if cls.confident(risk) and risk.choice == "routine":
            return Review(allowed=True)
        if cls.confident(authorization) and authorization.choice == "explicit":
            return Review(allowed=True)
        if risk.outcome == "accepted" and risk.choice in RISKS and risk.choice != "routine":
            return Review(allowed=False, category=risk.choice)
        return Review(allowed=False, category="unsure")

    @staticmethod
    def task(action: Action, messages: Sequence[object]) -> str:
        """What the classifier reads: the person's requests, the calls made since the
        latest, and the action. Never a tool result or the model's own words."""
        requests: list[str] = []
        latest = -1
        for index, message in enumerate(messages):
            if not isinstance(message, HumanMessage):
                continue
            provenance = Provenance.of(message)
            if provenance is None or not provenance.human:
                continue
            text = input_message_text(message.content)
            if not text or OPEN_TAG in text:
                continue
            requests.append(text[-_REQUEST_CHARS:])
            latest = index
        earlier = [
            Action(name=call["name"], arguments=call["args"], work_dir=None).summary()
            for message in messages[latest + 1 :]
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]
        return json.dumps(
            {
                "person_requests": requests[-_REQUESTS:],
                "working_directory": action.work_dir,
                "earlier_calls_this_turn": earlier[-_EARLIER_CALLS:],
                "action": action.summary(full_commands=True),
            },
            ensure_ascii=False,
        )
