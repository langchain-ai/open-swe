"""Shared shell token parsing for command guards."""

import os
import re
import shlex

_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
_SHELL_EXECUTABLES = {"bash", "dash", "sh", "zsh"}
_MAX_SHELL_EXPANSION_DEPTH = 3
_SHELL_EXPANSION_DEPTH_LIMIT_TOKEN = "__pr_creation_guard_shell_expansion_depth_limit__"
_GH_API_VALUE_FLAGS = {
    "-X",
    "--method",
    "-H",
    "--header",
    "-F",
    "--field",
    "-f",
    "--raw-field",
    "--hostname",
    "--input",
    "-q",
    "--jq",
    "-p",
    "--preview",
    "--cache",
    "-t",
    "--template",
}


def split_shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def executable_name(token: str) -> str:
    return os.path.basename(token.strip("'\""))


def shell_command_argument(tokens: list[str], shell_index: int) -> str | None:
    for index, token in enumerate(tokens[shell_index + 1 :], start=shell_index + 1):
        if token in _SHELL_SEPARATORS:
            return None
        if token == "-c" or (
            token.startswith("-") and not token.startswith("--") and "c" in token[1:]
        ):
            if index + 1 < len(tokens) and tokens[index + 1] not in _SHELL_SEPARATORS:
                return tokens[index + 1]
            return None
    return None


def _has_nested_shell_command(tokens: list[str]) -> bool:
    return any(
        executable_name(token) in _SHELL_EXECUTABLES
        and shell_command_argument(tokens, index) is not None
        for index, token in enumerate(tokens)
    )


def expand_nested_shell_tokens(tokens: list[str], depth: int = 0) -> list[str]:
    if depth >= _MAX_SHELL_EXPANSION_DEPTH:
        if _has_nested_shell_command(tokens):
            return [*tokens, _SHELL_EXPANSION_DEPTH_LIMIT_TOKEN]
        return tokens

    expanded = list(tokens)
    for index, token in enumerate(tokens):
        if executable_name(token) not in _SHELL_EXECUTABLES:
            continue
        inner_command = shell_command_argument(tokens, index)
        if inner_command is None:
            continue
        expanded.extend(expand_nested_shell_tokens(split_shell_tokens(inner_command), depth + 1))
    return expanded


def shell_tokens(command: str) -> list[str]:
    return expand_nested_shell_tokens(split_shell_tokens(command))


def is_assignment(token: str) -> bool:
    name, sep, _value = token.partition("=")
    return bool(sep and name and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def gh_subtokens(tokens: list[str], index: int) -> list[str]:
    subtokens: list[str] = []
    for token in tokens[index + 1 :]:
        if token in _SHELL_SEPARATORS:
            break
        subtokens.append(token)
    return subtokens


def gh_api_endpoint(subtokens: list[str]) -> str | None:
    for index, token in enumerate(subtokens):
        if token != "api":
            continue
        skip_next = False
        for candidate in subtokens[index + 1 :]:
            if skip_next:
                skip_next = False
                continue
            if candidate.startswith("-"):
                if "=" not in candidate and candidate in _GH_API_VALUE_FLAGS:
                    skip_next = True
                continue
            if is_assignment(candidate):
                continue
            return candidate.strip("'\"")
    return None


def gh_api_uses_mutation(subtokens: list[str]) -> bool:
    body_flags = {"-f", "--field", "-F", "--raw-field", "--input", "--json"}
    explicit_method: str | None = None
    for index, token in enumerate(subtokens):
        if token.startswith("-X") and token != "-X":
            explicit_method = token[2:].upper()
        elif token in {"-X", "--method"} and index + 1 < len(subtokens):
            explicit_method = subtokens[index + 1].upper()
        elif token.startswith("-X=") or token.startswith("--method="):
            explicit_method = token.split("=", 1)[1].upper()
        elif token in body_flags or any(token.startswith(f"{flag}=") for flag in body_flags):
            if explicit_method not in {"GET", "HEAD"}:
                return True
    return explicit_method in {"POST", "PATCH", "PUT", "DELETE"} or (
        explicit_method is None
        and any(
            token in body_flags or any(token.startswith(f"{flag}=") for flag in body_flags)
            for token in subtokens
        )
    )
