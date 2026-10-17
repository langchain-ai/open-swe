"""Scripts the backend copies into a thread's sandbox and runs there.

Each one is a standalone program: standard library only, and syntax that runs
on Python 3.8, since the sandbox's ``python3`` may be that old. ``make lint``
and ``make typecheck`` hold this directory to that version. The backend reads
and runs them through ``openswe.sandboxes.scripts``.
"""
