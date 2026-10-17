"""Scripts the backend copies into a thread's sandbox and runs there.

Each one is a standalone program: standard library only, and syntax that runs
on Python 3.8, since the sandbox's ``python3`` may be that old. ``make lint``
and ``make typecheck`` hold this directory to that version.
"""

from functools import cache
from importlib import resources


@cache
def source(name: str) -> str:
    """The text of the script ``name`` in this directory, e.g. ``"monitor.py"``."""
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8")
