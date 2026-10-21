"""Path helpers for managed memory mounts and local Context Hub mocks."""

from __future__ import annotations


def ensure_absolute(path: str) -> str:
    return f"/{trim_slashes(path)}"


def trim_slashes(path: str) -> str:
    return path.strip("/")


def strip_trailing_slash(path: str) -> str:
    return path.rstrip("/") if len(path) > 1 else path


def assert_safe_mounted_path(
    path: str,
    message: str = "Context Hub paths must not contain traversal segments",
) -> None:
    if "\\" in path or "\0" in path or any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError(message)
