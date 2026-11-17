"""Review cloud threads' local actions before the desktop applies native consent."""

import json
import logging
import posixpath
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_typesafe import Choice
from pydantic import JsonValue
from runtime_api.classifier import DEFAULT_JEV, JevDecision
from runtime_api.operations import LocalReview
from runtime_api.provenance import Provenance

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
    # A cloud thread's approved actions on the person's machine (``AutoReview.local``).
    "local_read_file": "path",
    "local_write_file": "path",
    "local_import_file": "path",
}
# The arguments of each tool that run as shell on the machine.
_SHELL_ARGUMENTS: Final = {
    "execute": ("command",),
    "background_execute": ("command",),
    "monitor": ("command", "filter"),
    "local_execute": ("command",),
}
# A cloud thread's tools that act on the person's machine through the desktop app, which
# runs them without asking only inside a grant the person gave the thread.
LOCAL_TOOLS: Final = frozenset(
    {"local_read_file", "local_write_file", "local_import_file", "local_execute"}
)

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
# Shell startup files, which often export tokens; a local action naming one always asks.
_SHELL_PROFILES: Final = frozenset(
    {".zshrc", ".zshenv", ".zprofile", ".bashrc", ".bash_profile", ".profile"}
)
# Runs of characters a path is written with, to find paths inside a longer shell word.
_PATHLIKE: Final = re.compile(r"[~\w.\-/]+")
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
_SYSTEM_PREFIXES: Final = ("/etc/", "/private/etc/", "/library/", "/system/")
# Never run through a cloud thread's local tools, whatever the person said.
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
# The classifier reads the shell of the call under review whole; a longer one needs consent.
_COMMAND_CHARS: Final = 10_000


@dataclass(frozen=True)
class Review:
    """Whether a local call may use a grant to skip the native dialog."""

    allowed: bool
    category: str = ""


@dataclass(frozen=True)
class Action:
    """A proposed call and its arguments for local review."""

    name: str
    arguments: Mapping[str, JsonValue]

    def forbidden(self) -> str | None:
        """The category of a command that cannot run through local tools."""
        for command in self._commands():
            for category, pattern in _FORBIDDEN:
                if pattern.search(command):
                    return category
        return None

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

    def names_credentials(self) -> bool:
        """Whether a call on the person's machine names a path that may hold credentials,
        in its path argument or anywhere in what it runs as shell."""
        names: list[str] = []
        if isinstance(path := self.arguments.get(PATH_ARGUMENTS.get(self.name, "")), str):
            names.append(path)
        for command in self._commands():
            try:
                names.extend(shlex.split(command))
            except ValueError:
                return True
            # Paths inside a word too, such as a script's `Path(".env")` in `python3 -c`.
            names.extend(_PATHLIKE.findall(command))
        return any(
            self._secret(name) or posixpath.basename(name.casefold()) in _SHELL_PROFILES
            for name in names
        )


class AutoReview:
    """Classify cloud threads' local actions without granting machine access."""

    @classmethod
    async def local(
        cls, name: str, arguments: Mapping[str, JsonValue], state: Mapping[str, object]
    ) -> LocalReview | str:
        """Refuse prohibited commands or decide whether native consent is required."""
        action = Action(name=name, arguments=arguments)
        if category := action.forbidden():
            logger.info(
                "auto_review",
                extra={"tool": name, "outcome": "forbid", "category": category},
            )
            reason = prompt("auto-review/reason", category=category)
            return prompt("auto-review/hard-denied", tool=name, reason=reason)
        if action.names_credentials():
            return LocalReview(cleared=False, concern="credential_access")
        if action.too_long():
            return LocalReview(cleared=False, concern="too_long")
        # A grant skips the person's dialog, so only a routine call clears; a risky one
        # they asked for still shows them the dialog, which is how they confirm it.
        review = await cls.review(action, state)
        return LocalReview(cleared=review.allowed, concern=review.category or None)

    @classmethod
    async def review(cls, action: Action, state: Mapping[str, object]) -> Review:
        """Clear only confidently routine calls, even when a risky call was requested."""
        messages = state.get("messages")
        transcript = messages if isinstance(messages, Sequence) else ()
        risk, authorization = JevDecision(), JevDecision()
        await DEFAULT_JEV.choose(
            cls.task(action, transcript),
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
        review = cls.verdict(risk)
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
    def confident(decision: JevDecision) -> bool:
        return (
            decision.outcome == "accepted"
            and decision.confidence is not None
            and decision.confidence >= ALLOW_CONFIDENCE
        )

    @classmethod
    def verdict(cls, risk: JevDecision) -> Review:
        """Clear a confidently routine call; otherwise name its concern."""
        if cls.confident(risk) and risk.choice == "routine":
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
            Action(name=call["name"], arguments=call["args"]).summary()
            for message in messages[latest + 1 :]
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]
        return json.dumps(
            {
                "person_requests": requests[-_REQUESTS:],
                "working_directory": None,
                "earlier_calls_this_turn": earlier[-_EARLIER_CALLS:],
                "action": action.summary(full_commands=True),
            },
            ensure_ascii=False,
        )
