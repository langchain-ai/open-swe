"""Zero-context diffs cut into content-keyed changed lines.

A line's key is its path, sign and text, never its position or commit, so a
line a person approved is recognized again after a rebase or force-push. A
file change with no text hunks (binary, mode, empty file) is keyed as one unit
by its header.
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_NEWLINE = "\\"


def line_key(path: str, sign: str, text: str) -> str:
    return hashlib.sha256(f"{path}\0{sign}\0{text}".encode()).hexdigest()


@dataclass
class _Hunk:
    old_start: int
    new_start: int
    lines: list[str] = field(default_factory=list)

    @property
    def atomic(self) -> bool:
        # Splitting around a missing final newline cannot be expressed line by line.
        return any(line.startswith(_NO_NEWLINE) for line in self.lines)

    def changed(self) -> list[str]:
        return [line for line in self.lines if line[:1] in ("+", "-")]


@dataclass
class _FileDiff:
    header: list[str]
    hunks: list[_Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        new = next((line[4:] for line in self.header if line.startswith("+++ ")), "")
        old = next((line[4:] for line in self.header if line.startswith("--- ")), "")
        if new and new != "/dev/null":
            return new.removeprefix("b/")
        if old and old != "/dev/null":
            return old.removeprefix("a/")
        return self.header[0].rpartition(" b/")[2]

    @property
    def unit_key(self) -> str:
        return line_key(self.path, "~", "\n".join(self.header[1:]))

    def keys(self) -> list[str]:
        if not self.hunks:
            return [self.unit_key]
        return [line_key(self.path, line[0], line[1:]) for h in self.hunks for line in h.changed()]


def parse(diff: str) -> list[_FileDiff]:
    files: list[_FileDiff] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files.append(_FileDiff(header=[line]))
        elif not files:
            continue
        elif match := _HUNK_RE.match(line):
            files[-1].hunks.append(_Hunk(old_start=int(match[1]), new_start=int(match[3])))
        elif files[-1].hunks:
            files[-1].hunks[-1].lines.append(line)
        else:
            files[-1].header.append(line)
    return files


def changed_line_keys(diff: str) -> Counter[str]:
    """Every changed line key in ``diff``, counted."""
    return Counter(key for file in parse(diff) for key in file.keys())


@dataclass
class Selection:
    patch: str = ""
    whole_files: list[str] = field(default_factory=list)
    lines: int = 0


def select_seen(diff: str, seen: Counter[str]) -> Selection:
    """The part of a ``-U0`` diff whose lines are already in ``seen``, consuming it.

    Unseen deletions become context and unseen additions are dropped, so the
    patch stages only seen lines. Files with no text hunks are returned whole.
    """
    remaining = Counter(seen)
    out: list[str] = []
    selection = Selection()
    for file in parse(diff):
        if not file.hunks:
            if remaining[file.unit_key] > 0:
                remaining[file.unit_key] -= 1
                selection.whole_files.append(file.path)
                selection.lines += 1
            continue
        path = file.path
        hunks: list[str] = []
        every_line = True
        for hunk in file.hunks:
            body, picked = _select_hunk(hunk, path, remaining)
            every_line = every_line and picked == len(hunk.changed())
            if picked:
                hunks.extend(body)
                selection.lines += picked
        if not hunks:
            continue
        header = [line for line in file.header if not line.startswith("index ")]
        if not every_line and any(line.startswith("deleted file mode") for line in header):
            header = [
                f"+++ b/{path}" if line == "+++ /dev/null" else line
                for line in header
                if not line.startswith("deleted file mode")
            ]
        out.extend(header)
        out.extend(hunks)
    selection.patch = "\n".join(out) + "\n" if out else ""
    return selection


def _select_hunk(hunk: _Hunk, path: str, remaining: Counter[str]) -> tuple[list[str], int]:
    keys = [line_key(path, line[0], line[1:]) for line in hunk.changed()]
    if hunk.atomic:
        need = Counter(keys)
        if any(remaining[key] < count for key, count in need.items()):
            return [], 0
        remaining.subtract(need)
        return _render(hunk, hunk.lines), len(keys)
    body: list[str] = []
    picked = 0
    for line in hunk.lines:
        key = line_key(path, line[0], line[1:])
        if remaining[key] > 0:
            remaining[key] -= 1
            body.append(line)
            picked += 1
        elif line.startswith("-"):
            body.append(f" {line[1:]}")
    return (_render(hunk, body), picked) if picked else ([], 0)


def _render(hunk: _Hunk, body: list[str]) -> list[str]:
    old = sum(1 for line in body if line[:1] in ("-", " "))
    new = sum(1 for line in body if line[:1] in ("+", " "))
    old_start = hunk.old_start if old or hunk.old_start else 0
    return [f"@@ -{old_start},{old} +{hunk.new_start},{new} @@", *body]
