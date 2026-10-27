"""Reflow Markdown and Markdown Jinja files to one sentence per line with rumdl.

rumdl reads a standalone `{% ... %}` line as prose and joins it into the paragraph
around it, which changes how the template renders. Those lines are swapped for HTML
comments, which rumdl leaves alone, for the length of the run.
"""

import re
import subprocess
import sys
from pathlib import Path

TAG_LINE = re.compile(r"^[ \t]*\{%-?.*-?%\}[ \t]*$|^[ \t]*\{#.*#\}[ \t]*$")
PLACEHOLDER = re.compile(r"^<!-- jinja:(\d+) -->$")


def protect(lines: list[str]) -> tuple[list[str], list[str]]:
    tags: list[str] = []
    out: list[str] = []
    for line in lines:
        if TAG_LINE.match(line):
            out.append(f"<!-- jinja:{len(tags)} -->")
            tags.append(line)
        else:
            out.append(line)
    return out, tags


def restore(lines: list[str], tags: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        match = PLACEHOLDER.match(line)
        out.append(tags[int(match.group(1))] if match else line)
    return out


def reflow(path: Path) -> None:
    original = path.read_text()
    protected, tags = protect(original.splitlines())
    path.write_text("\n".join(protected) + "\n")
    subprocess.run(["rumdl", "check", "--fix", "--quiet", str(path)], check=False)
    reflowed = restore(path.read_text().splitlines(), tags)
    path.write_text("\n".join(reflowed) + "\n")


def main(paths: list[str]) -> int:
    changed = 0
    for name in paths:
        path = Path(name)
        before = path.read_text()
        reflow(path)
        changed += path.read_text() != before
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
