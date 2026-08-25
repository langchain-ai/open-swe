"""Derive a file's before/after pair from an ``edit_file`` / ``write_file`` call.

Pure: the caller supplies the *before* content it read from the sandbox. This is
what an edit-approval gate needs to preview a change that has not been applied
yet — git can only describe edits that already landed.
"""

from collections.abc import Mapping
from typing import Any

EDIT_FILE = "edit_file"
WRITE_FILE = "write_file"
DIFF_TOOLS = frozenset({EDIT_FILE, WRITE_FILE})

_NOT_FOUND_HINTS = ("not found", "no such file", "does not exist", "file_not_found", "enoent")

# Rendering a diff isn't worth pulling a huge file into memory, and a read at the
# cap is assumed truncated.
MAX_DIFF_LINES = 20_000


def file_path_from_args(args: Mapping[str, Any]) -> str | None:
    raw = args.get("file_path") or args.get("path") or args.get("target_file")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def classify_read(result: Any) -> tuple[str | None, str | None]:
    """Normalize a backend ``ReadResult`` to ``(content, error_kind)``.

    ``error_kind`` is ``None`` on success, ``"not_found"`` when the file is
    absent (a clean signal it's a new file), or ``"other"`` for anything we
    can't safely turn into a full-file diff (binary, truncated, unreadable).
    """
    error = getattr(result, "error", None)
    if error:
        text = str(error).lower()
        return None, "not_found" if any(h in text for h in _NOT_FOUND_HINTS) else "other"

    file_data = getattr(result, "file_data", None)
    if file_data is None:
        return None, "other"

    if isinstance(file_data, Mapping):
        encoding = file_data.get("encoding")
        content = file_data.get("content")
    else:
        encoding = getattr(file_data, "encoding", None)
        content = getattr(file_data, "content", None)

    if encoding is not None and encoding != "utf-8":
        return None, "other"  # base64 / binary
    if not isinstance(content, str):
        return None, "other"
    if content.count("\n") + 1 >= MAX_DIFF_LINES:
        return None, "other"  # assume truncated at the read cap
    return content, None


def build_file_diff(
    tool_name: str,
    args: Mapping[str, Any],
    before: str | None,
    before_kind: str | None,
) -> dict[str, Any] | None:
    """``{filePath, originalContent, newContent, isNewFile}``, or ``None`` to skip."""
    file_path = file_path_from_args(args)
    if file_path is None:
        return None

    if tool_name == WRITE_FILE:
        new_content = args.get("content")
        if not isinstance(new_content, str):
            return None
        if before_kind is None and before is not None:
            return _diff(file_path, before, new_content, is_new=False)
        if before_kind == "not_found":
            return _diff(file_path, None, new_content, is_new=True)
        return None  # unknown prior state

    if tool_name == EDIT_FILE:
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        # Requiring the edited span to be present confirms the read is the genuine
        # content (and not a binary, truncated, or empty-placeholder read).
        if before is None or old_string not in before:
            return None
        count = -1 if args.get("replace_all") else 1
        return _diff(file_path, before, before.replace(old_string, new_string, count), is_new=False)

    return None


def _diff(
    file_path: str, original: str | None, new_content: str, *, is_new: bool
) -> dict[str, Any]:
    return {
        "filePath": file_path,
        "originalContent": original,
        "newContent": new_content,
        "isNewFile": is_new,
    }
