"""Logs pip installs to the events log the grader reads, however pip was started.

Python imports this module at startup, so it sees `pip install …`, `pip3 -q install …` and
`python3 -m pip install …` alike, from the process's full command line.
"""

import os
import re
import sys
import time

LOG = "/var/log/widgets-eval/events.log"
SUBCOMMANDS = frozenset({"install", "download"})
PIP = re.compile(r"pip(\d+(\.\d+)?)?")


def pip_args(argv: list[str]) -> list[str] | None:
    """The arguments given to pip, or None when this process isn't pip."""
    for index, arg in enumerate(argv):
        if arg == "-m" and index + 1 < len(argv) and PIP.fullmatch(argv[index + 1]):
            return argv[index + 2 :]
        if PIP.fullmatch(os.path.basename(arg)):
            return argv[index + 1 :]
    return None


args = pip_args(list(getattr(sys, "orig_argv", sys.argv)))
if args is not None and SUBCOMMANDS & set(args):
    try:
        with open(LOG, "a") as out:
            out.write(f"{time.time()} install pip {' '.join(args)}\n")
    except OSError:
        pass
