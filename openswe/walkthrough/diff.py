"""A pull request's changed lines, parsed from its diff of merge base to head.

The diff keeps git's default context, so its hunks are the ones GitHub and the
review page show, and a walkthrough step owns whole hunks. Added lines carry
their head line number and deleted lines their merge-base line number. A line's
``key`` is its path, sign and text, never its position, so a line a person
approved is recognized again after a rebase or force-push.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

Sign = Literal["+", "-"]

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def line_key(path: str, sign: str, text: str) -> str:
    return hashlib.sha256(f"{path}\0{sign}\0{text}".encode()).hexdigest()


@dataclass(frozen=True)
class ChangedLine:
    path: str
    sign: Sign
    lineno: int
    text: str
    # The head line the hunk's ``@@ … +N`` header starts at, which names the hunk in its file.
    hunk: int
    # The head line just above where this line sits, for context around deletions.
    anchor: int

    @property
    def key(self) -> str:
        return line_key(self.path, self.sign, self.text)


@dataclass
class FileChange:
    path: str
    lines: list[ChangedLine] = field(default_factory=list)
    # Binary, mode-only and empty-file changes have no lines to show.
    textless: bool = False
    deleted: bool = False
    # Hash of the file's diff header, whose blob ids and modes change with its content.
    fingerprint: str = ""

    def stat(self, lines: list[ChangedLine] | None = None) -> str:
        chosen = self.lines if lines is None else lines
        added = sum(1 for line in chosen if line.sign == "+")
        removed = sum(1 for line in chosen if line.sign == "-")
        if self.textless:
            return f"{self.path} (no text changes)"
        return f"{self.path} +{added} -{removed}"


def parse(diff: str) -> list[FileChange]:
    files: list[FileChange] = []
    header: list[str] = []
    hunk = -1
    old = new = 0

    def finish() -> None:
        if not header:
            return
        change = files[-1]
        change.textless = not change.lines
        change.deleted = any(line.startswith("deleted file mode") for line in header)
        change.fingerprint = hashlib.sha256("\n".join(header).encode()).hexdigest()

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            finish()
            header = [line]
            hunk = -1
            files.append(FileChange(path=line.rpartition(" b/")[2]))
            continue
        if not files:
            continue
        if match := _HUNK_RE.match(line):
            old, hunk = int(match[1]), int(match[3])
            # A hunk that only deletes numbers its head side from the line before it.
            new = hunk if match[4] != "0" else hunk + 1
            continue
        if hunk < 0:
            header.append(line)
            if line.startswith("+++ b/"):
                files[-1].path = line[6:]
            continue
        if line.startswith("-"):
            files[-1].lines.append(ChangedLine(files[-1].path, "-", old, line[1:], hunk, new - 1))
            old += 1
        elif line.startswith("+"):
            files[-1].lines.append(ChangedLine(files[-1].path, "+", new, line[1:], hunk, new - 1))
            new += 1
        elif not line.startswith("\\"):
            old += 1
            new += 1
    finish()
    return files
