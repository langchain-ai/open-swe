"""A channel's app icon, read from the project and served by the deployment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CHANNEL_ICON_MAX_BYTES = 1024 * 1024
"""Largest icon a channel serves."""

_ICON_TYPES: dict[str, tuple[str, bytes]] = {
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "jpg": ("image/jpeg", b"\xff\xd8\xff"),
    "jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}


@dataclass(frozen=True)
class ChannelIcon:
    """A channel icon read from the project, ready to serve."""

    data: bytes
    content_type: str


def _icon_extension(path: str) -> str | None:
    extension = PurePosixPath(path).suffix.lstrip(".").lower()
    return extension if extension in _ICON_TYPES else None


def validate_channel_icon_path(path: str, option: str) -> str:
    """Check an icon option: a path relative to ``channels/``, such as ``assets/icon.png``.

    As for the Slack channel's icon. Returns the path trimmed; raises
    ``ValueError`` unless it names a PNG or JPEG image inside ``channels/``.
    """
    trimmed = path.strip()
    segments = trimmed.split("/")
    if (
        not trimmed
        or trimmed.startswith("/")
        or "\\" in trimmed
        or (len(trimmed) > 1 and trimmed[1] == ":")
        or any(segment in ("", ".", "..") for segment in segments)
        or _icon_extension(trimmed) is None
    ):
        raise ValueError(
            f"{option} must be a path relative to channels/, such as assets/icon.png, "
            "of a PNG or JPEG image"
        )
    return trimmed


def load_channel_icon(root: str | os.PathLike[str], path: str) -> ChannelIcon:
    """Read a channel's icon from ``channels/`` in the project at ``root``.

    Only a PNG or JPEG file inside ``channels/``, of at most
    ``CHANNEL_ICON_MAX_BYTES``, whose content matches its extension, is served;
    anything else raises.
    """
    relative = validate_channel_icon_path(path, "icon")
    extension = _icon_extension(relative)
    if extension is None:  # pragma: no cover - validated above
        raise ValueError("icon is not a PNG or JPEG image")
    real_root = (Path(root) / "channels").resolve(strict=True)
    # Resolve links before the containment check, so none leads out.
    file = (real_root / relative).resolve(strict=True)
    if file == real_root or not file.is_relative_to(real_root):
        raise ValueError("icon is outside channels/")
    if not file.is_file():
        raise ValueError("icon is not a file")
    size = file.stat().st_size
    if size == 0 or size > CHANNEL_ICON_MAX_BYTES:
        raise ValueError(f"icon must be a file of at most {CHANNEL_ICON_MAX_BYTES} bytes")
    data = file.read_bytes()
    content_type, magic = _ICON_TYPES[extension]
    if not data.startswith(magic):
        raise ValueError(f"icon is not a {extension.upper()} image")
    return ChannelIcon(data=data, content_type=content_type)


def channel_icon_headers(icon: ChannelIcon) -> dict[str, str]:
    """Headers for a served icon: an image that never runs as a page."""
    return {
        "Content-Type": icon.content_type,
        "Cache-Control": "public, max-age=3600",
        "Content-Security-Policy": "default-src 'none'",
        "X-Content-Type-Options": "nosniff",
    }
