"""Keep a log value within the collection endpoint's per-value limit.

The middle is cut, as the CLI does: the start says what failed, and the end of
a traceback or a child's output usually says why.
"""

from __future__ import annotations

from typing import Final

#: The collection endpoint keeps up to this many bytes of a value.
MAX_VALUE_BYTES: Final = 32 << 10
#: Kept from the start of a value that is too long; the rest keeps its end.
_HEAD_BYTES: Final = 1 << 10
#: Room for the omission marker.
_MARKER_BYTES: Final = 48
#: UTF-8 encodes a code point in at most this many bytes.
_MAX_CODE_POINT_BYTES: Final = 4


def truncate_middle(value: str, max_bytes: int = MAX_VALUE_BYTES) -> str:
    """``value`` within ``max_bytes`` of UTF-8: its start and end, with the cut size."""
    if len(value) * _MAX_CODE_POINT_BYTES <= max_bytes:
        return value
    encoded = value.encode("utf-8", "surrogatepass")
    if len(encoded) <= max_bytes:
        return value
    head_end = _floor_boundary(encoded, _HEAD_BYTES)
    tail_start = _ceil_boundary(encoded, len(encoded) - (max_bytes - _HEAD_BYTES - _MARKER_BYTES))
    head = encoded[:head_end].decode("utf-8", "surrogatepass")
    tail = encoded[tail_start:].decode("utf-8", "surrogatepass")
    return f"{head}\n… {tail_start - head_end} bytes omitted …\n{tail}"


def _is_continuation(encoded: bytes, index: int) -> bool:
    return index < len(encoded) and encoded[index] & 0xC0 == 0x80  # noqa: PLR2004


def _floor_boundary(encoded: bytes, index: int) -> int:
    while index > 0 and _is_continuation(encoded, index):
        index -= 1
    return index


def _ceil_boundary(encoded: bytes, index: int) -> int:
    while _is_continuation(encoded, index):
        index += 1
    return index
