"""Reading part of a sandbox file without downloading all of it."""

from typing import Protocol, runtime_checkable


class FileTooLargeError(Exception):
    """The file outgrew the limit its reader was held to."""


@runtime_checkable
class RangeReadable(Protocol):
    """A sandbox backend that can return a byte range of a file.

    Backends without it, such as a bridge to the user's own machine, are read whole.
    """

    async def aread_range(self, path: str, start: int, end: int, *, limit: int) -> bytes:
        """Bytes ``start`` to ``end`` inclusive of the absolute ``path``, clamped to its end.

        Raises ``FileNotFoundError`` when the file is gone, and ``FileTooLargeError``
        when it has grown past ``limit`` bytes.
        """
        ...
