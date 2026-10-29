"""Immutable per-run backend and resolver for selected skills."""

from __future__ import annotations

import json
from collections.abc import Mapping
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path
from typing import cast

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from managed_deepagents._managed_composition import (
    skill_name_from_path,
    snapshot_skill_selection,
    valid_skill_file_path,
    valid_skill_name,
)
from managed_deepagents._memory.context_io import as_text, result_value
from managed_deepagents._memory.paths import assert_safe_mounted_path

MAX_SELECTED_SKILL_FILES = 128
MAX_SELECTED_SKILL_FILE_BYTES = 1024 * 1024
MAX_SELECTED_SKILL_TOTAL_BYTES = 10 * 1024 * 1024
_MAX_PATH_LENGTH = 1024
_MAX_PATH_SEGMENTS = 32
_MAX_GLOB_LENGTH = 512
_SKILLS_ROOT = "/skills"


def normalize_selected_skill_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or len(path) > _MAX_PATH_LENGTH:
        raise ValueError("invalid skills path")
    assert_safe_mounted_path(path, "invalid skills path")
    parts = [part for part in path.split("/") if part]
    if len(parts) > _MAX_PATH_SEGMENTS:
        raise ValueError("invalid skills path")
    return "/" + "/".join(parts)


def _safe_glob(pattern: str, root: str) -> str:
    if (
        not pattern
        or len(pattern) > _MAX_GLOB_LENGTH
        or any(char in pattern for char in "\\\0[]{}")
    ):
        raise ValueError("invalid skills glob")
    assert_safe_mounted_path(pattern, "invalid skills glob")
    if len([part for part in pattern.split("/") if part]) > _MAX_PATH_SEGMENTS:
        raise ValueError("invalid skills glob")
    bare = pattern.removeprefix("/")
    return (
        f"/{bare.removeprefix('skills/')}"
        if root == "/" and bare.startswith("skills/")
        else pattern
    )


def _relative_search_path(file: str, root: str) -> str | None:
    if file == root:
        return file.rsplit("/", 1)[-1]
    prefix = "/" if root == "/" else f"{root}/"
    return file[len(prefix) :] if file.startswith(prefix) else None


def _glob_match(pattern: str, value: str) -> bool:
    patterns = [part for part in pattern.split("/") if part]
    parts = [part for part in value.split("/") if part]
    if "/" not in pattern:
        return bool(parts) and fnmatchcase(parts[-1], pattern)

    @cache
    def visit(pattern_index: int, value_index: int) -> bool:
        if pattern_index == len(patterns):
            return value_index == len(parts)
        if patterns[pattern_index] == "**":
            return visit(pattern_index + 1, value_index) or (
                value_index < len(parts) and visit(pattern_index, value_index + 1)
            )
        return (
            value_index < len(parts)
            and fnmatchcase(parts[value_index], patterns[pattern_index])
            and visit(pattern_index + 1, value_index + 1)
        )

    return visit(0, 0)


class SelectedSkillsBackend(BackendProtocol):
    """Read-only backend containing only one run's selected skill files."""

    def __init__(self, full_paths: Mapping[str, str]) -> None:
        if len(full_paths) > MAX_SELECTED_SKILL_FILES:
            raise ValueError("selected skills exceed the file limit")
        files: dict[str, str] = {}
        total_bytes = 0
        for full_path, content in full_paths.items():
            normalized = normalize_selected_skill_path(full_path)
            if not normalized.startswith("/skills/") or not isinstance(content, str):
                raise ValueError("invalid selected skill file")
            size = len(content.encode())
            if size > MAX_SELECTED_SKILL_FILE_BYTES:
                raise ValueError("selected skill file exceeds the size limit")
            total_bytes += size
            if total_bytes > MAX_SELECTED_SKILL_TOTAL_BYTES:
                raise ValueError("selected skills exceed the total size limit")
            files[normalized[len("/skills") :]] = content
        self._files = files

    @property
    def file_count(self) -> int:
        return len(self._files)

    def ls(self, path: str = "/") -> LsResult:
        root = normalize_selected_skill_path(path)
        prefix = "/" if root == "/" else f"{root}/"
        entries: dict[str, FileInfo] = {}
        for file in self._files:
            if not file.startswith(prefix):
                continue
            rest = file[len(prefix) :]
            first = rest.split("/", 1)[0]
            if not first:
                continue
            directory = "/" in rest
            entry = f"{prefix}{first}{'/' if directory else ''}"
            entries[entry] = {"path": entry, "is_dir": directory}
        return LsResult(entries=list(entries.values()))

    async def als(self, path: str = "/") -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        content = self._files.get(normalize_selected_skill_path(file_path))
        if content is None:
            return ReadResult(error="file_not_found")
        if offset < 0 or limit < 0:
            return ReadResult(error="invalid_path")
        lines = content.splitlines(keepends=True)
        if not lines:
            return (
                ReadResult(file_data={"content": "", "encoding": "utf-8"})
                if offset == 0
                else ReadResult(error="line_offset_out_of_range")
            )
        if offset >= len(lines):
            return ReadResult(error="line_offset_out_of_range")
        if limit == 0:
            return ReadResult(
                file_data={"content": "", "encoding": "utf-8"}, no_lines_requested=True
            )
        end = min(len(lines), offset + limit)
        return ReadResult(
            file_data={"content": "".join(lines[offset:end]), "encoding": "utf-8"},
            total_lines=len(lines),
            start_line=offset + 1,
            end_line=end,
            next_offset=end if end < len(lines) else None,
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self.read(file_path, offset, limit)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        root = normalize_selected_skill_path(path or "/")
        safe_pattern = _safe_glob(pattern, root)
        matches: list[FileInfo] = []
        for file in self._files:
            relative = _relative_search_path(file, root)
            if relative is not None and _glob_match(safe_pattern, relative):
                matches.append({"path": file, "is_dir": False})
        return GlobResult(matches=matches)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.glob(pattern, path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if max_count is not None and max_count <= 0:
            return GrepResult(matches=[], truncated=True)
        root = normalize_selected_skill_path(path or "/")
        safe_glob = _safe_glob(glob, root) if glob else None
        matches: list[GrepMatch] = []
        for file, content in self._files.items():
            relative = _relative_search_path(file, root)
            if relative is None:
                continue
            if safe_glob and not _glob_match(safe_glob, relative):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if pattern in line:
                    matches.append({"path": file, "line": line_number, "text": line})
                if max_count is not None and len(matches) >= max_count:
                    return GrepResult(matches=matches, truncated=True)
        return GrepResult(matches=matches)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self.grep(pattern, path, glob, max_count=max_count)

    def write(self, file_path: str, content: str) -> WriteResult:
        del file_path, content
        return WriteResult(error="permission_denied")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        del file_path, old_string, new_string, replace_all
        return EditResult(error="permission_denied")

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        del file_path
        return DeleteResult(error="permission_denied")

    async def adelete(self, file_path: str) -> DeleteResult:
        return self.delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results: list[FileDownloadResponse] = []
        for path in paths:
            content = self._files.get(normalize_selected_skill_path(path))
            results.append(
                FileDownloadResponse(
                    path=path,
                    content=content.encode() if content is not None else None,
                    error=None if content is not None else "file_not_found",
                )
            )
        return results

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.download_files(paths)


def skill_markdown(skill: Mapping[str, object]) -> str:
    return (
        f"---\nname: {skill['name']}\n"
        f"description: {json.dumps(skill['description'], ensure_ascii=True)}\n"
        f"---\n\n{skill['markdown']}\n"
    )


def resolve_selected_skills(
    selection: object,
    *,
    context_backend: BackendProtocol | None = None,
    local_root: str | None = None,
) -> SelectedSkillsBackend:
    normalized = _normalize_selection(selection)
    if not normalized:
        return SelectedSkillsBackend({})
    inventory = (
        _local_skill_inventory(local_root)
        if local_root is not None
        else _backend_skill_inventory(context_backend)
        if context_backend is not None
        else set()
    )
    files: dict[str, str] = {}
    for skill in normalized:
        if isinstance(skill, str):
            if skill not in inventory:
                raise ValueError("selected skill path does not reference an authored skill")
            copied = (
                _copy_local_skill(local_root, skill)
                if local_root is not None
                else _copy_backend_skill(cast("BackendProtocol", context_backend), skill)
            )
            for path, content in copied.items():
                _add_selected_file(files, path, content)
            continue
        name = cast("str", skill["name"])
        if name in inventory:
            raise ValueError("generated skill name conflicts with an authored skill")
        _add_selected_file(
            files,
            f"{_SKILLS_ROOT}/{name}/SKILL.md",
            skill_markdown(skill),
        )
        support = skill.get("files")
        if isinstance(support, Mapping):
            for path, content in support.items():
                _add_selected_file(
                    files,
                    f"{_SKILLS_ROOT}/{name}/{path}",
                    cast("str", content),
                )
    return SelectedSkillsBackend(files)


def _normalize_selection(selection: object) -> tuple[str | Mapping[str, object], ...]:
    result: list[str | Mapping[str, object]] = []
    for item in snapshot_skill_selection(selection):
        if not isinstance(item, str):
            result.append(item)
            continue
        name = skill_name_from_path(item)
        if name is None:
            raise TypeError("skills contains an invalid path")
        result.append(name)
    return tuple(result)


def _backend_entries(result: object) -> list[object]:
    value = result_value(result, "entries") or result_value(result, "files")
    return value if isinstance(value, list) else []


def _backend_skill_inventory(backend: BackendProtocol) -> set[str]:
    result = backend.ls(_SKILLS_ROOT)
    if result_value(result, "error"):
        return set()
    names: set[str] = set()
    for entry in _backend_entries(result):
        path = _normalize_inventory_path(result_value(entry, "path"))
        name = path[len(f"{_SKILLS_ROOT}/") :].split("/", 1)[0]
        if valid_skill_name(name):
            names.add(name)
    return names


def _normalize_inventory_path(path: object) -> str:
    if not isinstance(path, str):
        raise ValueError("root skills inventory contains an invalid path")
    normalized = normalize_selected_skill_path(path)
    if not normalized.startswith(f"{_SKILLS_ROOT}/"):
        raise ValueError("root skills inventory contains an invalid path")
    return normalized


def _copy_backend_skill(backend: BackendProtocol, name: str) -> dict[str, str]:  # noqa: C901
    root = f"{_SKILLS_ROOT}/{name}"
    files: dict[str, str] = {}
    visited: set[str] = set()

    def walk(directory: str) -> None:  # noqa: C901
        normalized_directory = _normalize_inventory_path(directory)
        if normalized_directory != root and not normalized_directory.startswith(f"{root}/"):
            raise ValueError("selected skill contains an invalid path")
        if normalized_directory in visited:
            return
        visited.add(normalized_directory)
        result = backend.ls(normalized_directory)
        if result_value(result, "error") or not _backend_entries(result):
            if normalized_directory == root:
                raise ValueError("selected skill is not readable")
            return
        for entry in _backend_entries(result):
            path = _normalize_inventory_path(result_value(entry, "path"))
            if path != root and not path.startswith(f"{root}/"):
                raise ValueError("selected skill contains an invalid path")
            is_directory = bool(result_value(entry, "is_dir")) or path.endswith("/")
            if is_directory:
                walk(path)
                continue
            _validate_copied_file_path(path[len(f"{root}/") :])
            size = result_value(entry, "size")
            if isinstance(size, int) and size > MAX_SELECTED_SKILL_FILE_BYTES:
                raise ValueError("selected skill file exceeds the size limit")
            responses = backend.download_files([path])
            response = responses[0] if responses else None
            if response is None or result_value(response, "error"):
                raise ValueError("selected skill file is not readable")
            content = result_value(response, "content")
            if content is None:
                raise ValueError("selected skill file is not readable")
            try:
                _add_selected_file(files, path, as_text(content))
            except UnicodeDecodeError:
                raise ValueError("selected skill file must be UTF-8 text") from None

    walk(root)
    _assert_skill_markdown(files, root)
    return files


def _local_skill_inventory(local_root: str) -> set[str]:
    root = Path(local_root, "skills")
    try:
        entries = tuple(root.iterdir())
    except OSError:
        return set()
    names: set[str] = set()
    for entry in entries:
        if valid_skill_name(entry.name) and _contained_path(root, entry).is_dir():
            names.add(entry.name)
    return names


def _copy_local_skill(local_root: str, name: str) -> dict[str, str]:
    skills_root = Path(local_root, "skills")
    skill_root = _contained_path(skills_root, skills_root / name)
    if not skill_root.is_dir():
        raise ValueError("selected skill is not a directory")
    files: dict[str, str] = {}
    visited: set[Path] = set()

    def walk(physical_directory: Path, logical_directory: str) -> None:
        canonical = _contained_path(skills_root, physical_directory)
        if canonical in visited:
            raise ValueError("selected skill contains an invalid symlink")
        visited.add(canonical)
        for entry in canonical.iterdir():
            logical = f"{logical_directory}/{entry.name}" if logical_directory else entry.name
            _validate_copied_file_path(logical)
            resolved = _contained_path(skills_root, entry)
            if resolved.is_dir():
                walk(resolved, logical)
            elif resolved.is_file():
                if resolved.stat().st_size > MAX_SELECTED_SKILL_FILE_BYTES:
                    raise ValueError("selected skill file exceeds the size limit")
                try:
                    _add_selected_file(
                        files,
                        f"{_SKILLS_ROOT}/{name}/{logical}",
                        resolved.read_text(),
                    )
                except UnicodeDecodeError:
                    raise ValueError("selected skill file must be UTF-8 text") from None
            else:
                raise ValueError("selected skill contains an unsupported file")

    walk(skill_root, "")
    _assert_skill_markdown(files, f"{_SKILLS_ROOT}/{name}")
    return files


def _validate_copied_file_path(path: str) -> None:
    if path != "SKILL.md" and not valid_skill_file_path(path):
        raise ValueError("selected skill contains an invalid file path")


def _assert_skill_markdown(files: Mapping[str, str], root: str) -> None:
    if f"{root}/SKILL.md" not in files:
        raise ValueError("selected skill is missing SKILL.md")


def _add_selected_file(files: dict[str, str], path: str, content: str) -> None:
    if path not in files and len(files) >= MAX_SELECTED_SKILL_FILES:
        raise ValueError("selected skills exceed the file limit")
    size = len(content.encode())
    if size > MAX_SELECTED_SKILL_FILE_BYTES:
        raise ValueError("selected skill file exceeds the size limit")
    total = size + sum(
        len(existing.encode()) for existing_path, existing in files.items() if existing_path != path
    )
    if total > MAX_SELECTED_SKILL_TOTAL_BYTES:
        raise ValueError("selected skills exceed the total size limit")
    files[path] = content


def _contained_path(root: Path, candidate: Path) -> Path:
    try:
        canonical_root = root.resolve(strict=True)
        canonical_candidate = candidate.resolve(strict=True)
        canonical_candidate.relative_to(canonical_root)
    except (OSError, ValueError):
        raise ValueError("selected skill path escapes the skills directory") from None
    return canonical_candidate
