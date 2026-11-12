from types import SimpleNamespace

import pytest
from langsmith.sandbox import FileChunk

from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox
from openswe.sandboxes.ranges import FileTooLargeError

CONTENT = bytes(range(100))


class _RangeServer:
    """A dataplane that honors ranges or ignores them."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def read_range(self, path: str, *, start: int, end: int) -> FileChunk:
        if self.mode == "whole":
            return FileChunk(content=CONTENT, start=0, partial=False)
        return FileChunk(
            content=CONTENT[start : end + 1], start=start, partial=True, total_bytes=len(CONTENT)
        )


def _backend(mode: str) -> TimeoutLangSmithSandbox:
    sb = TimeoutLangSmithSandbox.__new__(TimeoutLangSmithSandbox)
    object.__setattr__(sb, "_sandbox", SimpleNamespace(name="sandbox-test"))
    object.__setattr__(sb, "_async_sandbox", _RangeServer(mode))
    object.__setattr__(sb, "_async_client", None)
    return sb


@pytest.mark.parametrize("mode", ["ranged", "whole"])
async def test_range_read_returns_only_the_requested_bytes(mode: str) -> None:
    assert await _backend(mode).aread_range("/w/clip.mp4", 10, 19, limit=100) == CONTENT[10:20]
    assert await _backend(mode).aread_range("/w/clip.mp4", 95, 200, limit=100) == CONTENT[95:]


@pytest.mark.parametrize("mode", ["ranged", "whole"])
async def test_range_read_refuses_a_file_that_outgrew_the_limit(mode: str) -> None:
    with pytest.raises(FileTooLargeError):
        await _backend(mode).aread_range("/w/clip.mp4", 0, 9, limit=99)
