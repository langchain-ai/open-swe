"""Validation and immutable snapshots for dynamic managed resources."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import NotRequired, TypedDict, cast

from managed_deepagents._connectors.mcp import McpServersDefinition
from managed_deepagents._memory.layers import normalize_memory_layers
from managed_deepagents.define_browser import BrowserDefinition
from managed_deepagents.define_memory import MemoryDefinition
from managed_deepagents.sandbox import SandboxDefinition

McpSelection = Sequence[McpServersDefinition]
_MEMORY_CONFIG_VERSION = 2
DYNAMIC_MEMORY_ALLOW_ERROR = (
    "Dynamically selected memory cannot use `allow`. Select memory layers or return "
    "None from the dynamic agent resolver instead."
)


class GeneratedSkill(TypedDict):
    name: str
    description: str
    markdown: str
    files: NotRequired[Mapping[str, str]]


SkillSelection = Sequence[str | GeneratedSkill]


class Omitted:
    __slots__ = ()


OMITTED = Omitted()
_SKILL_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_SKILL_FILE_SEGMENT = re.compile(r"[A-Za-z0-9._-]+\Z")
_SKILL_PATH_SUFFIX_LENGTH = 2


def valid_skill_name(name: str) -> bool:
    return _SKILL_NAME.fullmatch(name) is not None


def skill_name_from_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if not value or "\0" in value:
        return None
    normalized = value.replace("\\", "/")
    relative = normalized.removeprefix("./")
    segments = [segment for segment in relative.split("/") if segment]
    if (
        len(segments) < _SKILL_PATH_SUFFIX_LENGTH
        or any(segment in {".", ".."} for segment in segments)
        or segments[-_SKILL_PATH_SUFFIX_LENGTH] != "skills"
        or not valid_skill_name(segments[-1])
    ):
        return None
    return segments[-1]


def valid_skill_file_path(path: str) -> bool:
    if not path or path.startswith("/") or any(char in path for char in "\\\0:"):
        return False
    return all(
        _SKILL_FILE_SEGMENT.fullmatch(segment) is not None
        and segment not in {".", "..", "SKILL.md", "__proto__", "prototype", "constructor"}
        for segment in path.split("/")
    )


def snapshot_mcp_selection(value: object) -> tuple[McpServersDefinition, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("mcp must be a list")
    definitions: list[McpServersDefinition] = []
    seen: set[int] = set()
    for definition in value:
        if not isinstance(definition, McpServersDefinition):
            raise TypeError("mcp contains an invalid definition")
        identity = id(definition)
        if identity in seen:
            raise TypeError("mcp contains duplicate definitions")
        seen.add(identity)
        definitions.append(definition)
    return tuple(definitions)


def snapshot_memory_selection(value: object) -> MemoryDefinition:
    """Validate and copy a memory definition selected for a run."""
    if not isinstance(value, MemoryDefinition):
        raise TypeError("memory must be created with define_memory(...)")
    config = value.config
    if config.get("version") != _MEMORY_CONFIG_VERSION or not isinstance(
        config.get("scope"), Sequence
    ):
        raise TypeError("memory must be created with define_memory(...)")
    try:
        scope = normalize_memory_layers(config["scope"])
    except (KeyError, TypeError, ValueError):
        raise TypeError("memory must be created with define_memory(...)") from None
    return MemoryDefinition({"version": _MEMORY_CONFIG_VERSION, "scope": scope})


def assert_dynamic_memory_selection(memory: MemoryDefinition) -> None:
    """Reject the retired policy callback only for explicit dynamic selection."""
    if any("allow" in layer for layer in memory.config["scope"]):
        raise ValueError(DYNAMIC_MEMORY_ALLOW_ERROR)


def _snapshot_sandbox_value(value: object, copies: dict[int, object]) -> object:
    identity = id(value)
    if isinstance(value, Mapping):
        existing = copies.get(identity)
        if existing is not None:
            return existing
        copied_mapping: dict[object, object] = {}
        copies[identity] = copied_mapping
        copied_mapping.update(
            {key: _snapshot_sandbox_value(item, copies) for key, item in value.items()}
        )
        return copied_mapping
    if isinstance(value, list):
        existing = copies.get(identity)
        if existing is not None:
            return existing
        copied_list: list[object] = []
        copies[identity] = copied_list
        copied_list.extend(_snapshot_sandbox_value(item, copies) for item in value)
        return copied_list
    if isinstance(value, tuple):
        copied_tuple = tuple(_snapshot_sandbox_value(item, copies) for item in value)
        copies[identity] = copied_tuple
        return copied_tuple
    return value


def snapshot_sandbox_selection(value: object) -> SandboxDefinition | None:
    if value is None:
        return value
    if isinstance(value, SandboxDefinition):
        options = _snapshot_sandbox_value(value.options, {})
        if not isinstance(options, dict):
            raise TypeError("sandbox options must be a mapping")
        return SandboxDefinition(cast("dict[str, object]", options))
    raise TypeError("sandbox must be a SandboxDefinition or None")


def snapshot_browser_selection(value: object) -> BrowserDefinition | None:
    if value is None or isinstance(value, BrowserDefinition):  # frozen: safe to share
        return value
    raise TypeError("browser must be a BrowserDefinition or None")


def _snapshot_generated_skill(item: Mapping[str, object]) -> Mapping[str, object]:
    name = item.get("name")
    description = item.get("description")
    markdown = item.get("markdown")
    if (
        not isinstance(name, str)
        or not valid_skill_name(name)
        or not isinstance(description, str)
        or not description.strip()
        or not isinstance(markdown, str)
        or set(item) - {"name", "description", "markdown", "files"}
    ):
        raise TypeError("skills contains an invalid generated skill")
    fields: dict[str, object] = {
        "name": name,
        "description": description,
        "markdown": markdown,
    }
    if "files" in item:
        files = item["files"]
        if not isinstance(files, Mapping):
            raise TypeError("skills files must be a mapping of text files")
        copied_files: dict[str, str] = {}
        for path, content in files.items():
            if (
                not isinstance(path, str)
                or not valid_skill_file_path(path)
                or not isinstance(content, str)
            ):
                raise TypeError("skills files contain an invalid path or content")
            copied_files[path] = content
        fields["files"] = MappingProxyType(copied_files)
    return MappingProxyType(fields)


def snapshot_skill_selection(value: object) -> tuple[str | Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("skills must be a list")
    names: set[str] = set()
    skills: list[str | Mapping[str, object]] = []
    for item in value:
        if isinstance(item, str):
            name = skill_name_from_path(item)
            if name is None:
                raise TypeError("skills contains an invalid path")
            skill: str | Mapping[str, object] = item
        elif isinstance(item, Mapping):
            skill = _snapshot_generated_skill(item)
            name = cast("str", skill["name"])
        else:
            raise TypeError("skills contains an invalid skill")
        if name in names:
            raise TypeError("skills contains duplicate names")
        names.add(name)
        skills.append(skill)
    return tuple(skills)


def copy_managed_selections(
    instructions: str | Omitted | None,
    mcp: McpSelection | Omitted | None,
    sandbox: SandboxDefinition | Omitted | None,
    skills: SkillSelection | Omitted | None,
    memory: MemoryDefinition | Omitted | None,
    browser: BrowserDefinition | Omitted | None,
) -> dict[str, object]:
    selected: dict[str, object] = {}
    if not isinstance(instructions, Omitted):
        if instructions is not None and not isinstance(instructions, str):
            raise TypeError("instructions must be a string or None")
        selected["instructions"] = instructions
    if not isinstance(mcp, Omitted):
        selected["mcp"] = None if mcp is None else snapshot_mcp_selection(mcp)
    if not isinstance(sandbox, Omitted):
        selected["sandbox"] = snapshot_sandbox_selection(sandbox)
    if not isinstance(skills, Omitted):
        selected["skills"] = None if skills is None else snapshot_skill_selection(skills)
    if not isinstance(memory, Omitted):
        selected["memory"] = None if memory is None else snapshot_memory_selection(memory)
    if not isinstance(browser, Omitted):
        selected["browser"] = snapshot_browser_selection(browser)
    return selected


def snapshot_managed_composition(config: Mapping[str, object]) -> dict[str, object]:
    """Copy a definition config and validate its managed selection fields."""
    copied = dict(config)
    if (
        "instructions" in copied
        and copied["instructions"] is not None
        and not isinstance(copied["instructions"], str)
    ):
        raise TypeError("instructions must be a string or None")
    if "mcp" in copied:
        copied["mcp"] = None if copied["mcp"] is None else snapshot_mcp_selection(copied["mcp"])
    if "memory" in copied:
        copied["memory"] = (
            None if copied["memory"] is None else snapshot_memory_selection(copied["memory"])
        )
    if "sandbox" in copied:
        copied["sandbox"] = snapshot_sandbox_selection(copied["sandbox"])
    if "browser" in copied:
        copied["browser"] = snapshot_browser_selection(copied["browser"])
    if "skills" in copied:
        copied["skills"] = (
            None if copied["skills"] is None else snapshot_skill_selection(copied["skills"])
        )
    return copied
