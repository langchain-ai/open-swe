"""Chunks as a reader sees them: only their own lines, split into runs at real head line numbers.

A run that only adds code is shown as that source; a run that changes or
removes lines is shown as a diff with a little unchanged context. Lines left
to Other, such as the imports at the top of a new file, never appear inside a
chunk.
"""

import posixpath
from collections.abc import Mapping

from openswe.walkthrough.diff import ChangedLine

CONTEXT_LINES = 2

_LANGUAGES = {
    "py": "python",
    "ts": "typescript",
    "tsx": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "go": "go",
    "rs": "rust",
    "sql": "sql",
    "sh": "bash",
    "yml": "yaml",
    "yaml": "yaml",
    "json": "json",
    "md": "markdown",
    "css": "css",
    "html": "html",
}


def language_for(path: str) -> str:
    extension = posixpath.splitext(path)[1][1:].lower()
    return _LANGUAGES.get(extension, extension)


def fenced(body: str, language: str = "") -> str:
    return f"```{language}\n{body.rstrip()}\n```" if body.strip() else "_(nothing to show)_"


def _runs(lines: list[ChangedLine]) -> list[list[ChangedLine]]:
    """Split chosen lines at hunk boundaries and where their numbering jumps."""
    runs: list[list[ChangedLine]] = []
    for line in lines:
        last = runs[-1][-1] if runs else None
        replacing = last is not None and last.sign == "-" and line.sign == "+"
        contiguous = last is not None and last.sign == line.sign and line.lineno == last.lineno + 1
        if last is None or last.hunk != line.hunk or not (replacing or contiguous):
            runs.append([])
        runs[-1].append(line)
    return runs


def _context(head: list[str], changed: set[int], start: int, step: int) -> list[int]:
    """Up to ``CONTEXT_LINES`` unchanged head line numbers walking from ``start`` by ``step``."""
    found: list[int] = []
    lineno = start
    while len(found) < CONTEXT_LINES and 1 <= lineno <= len(head) and lineno not in changed:
        found.append(lineno)
        lineno += step
    return sorted(found)


def render_chunk(
    lines: list[ChangedLine], head: Mapping[str, list[str]], added: Mapping[str, set[int]]
) -> str:
    """Diff blocks per file for exactly ``lines``, in their order."""
    blocks: list[str] = []
    by_path: dict[str, list[ChangedLine]] = {}
    for line in lines:
        by_path.setdefault(line.path, []).append(line)
    for path, path_lines in by_path.items():
        text = head.get(path, [])
        changed = added.get(path, set())
        for run in _runs(path_lines):
            plus = [line for line in run if line.sign == "+"]
            minus = [line for line in run if line.sign == "-"]
            if not minus:
                span = f"L{plus[0].lineno}" + (f"–{plus[-1].lineno}" if len(plus) > 1 else "")
                source = "\n".join(line.text for line in plus)
                blocks.append(f"`{path}` {span}\n{fenced(source, language_for(path))}")
                continue
            first_new = plus[0].lineno if plus else run[0].anchor + 1
            last_new = plus[-1].lineno if plus else run[0].anchor
            before = _context(text, changed, first_new - 1, -1)
            after = _context(text, changed, last_new + 1, 1)
            new_start = before[0] if before else first_new
            old_start = minus[0].lineno - len(before)
            body = [
                f"@@ -{old_start},{len(before) + len(minus) + len(after)} "
                f"+{new_start},{len(before) + len(plus) + len(after)} @@",
                *(f" {text[n - 1]}" for n in before),
                *(f"{line.sign}{line.text}" for line in run),
                *(f" {text[n - 1]}" for n in after),
            ]
            blocks.append(f"`{path}`\n{fenced(chr(10).join(body), 'diff')}")
    return "\n".join(blocks)
