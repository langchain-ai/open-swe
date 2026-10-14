"""A unified diff parsed into numbered lines, with per-line syntax tokens."""

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import accumulate, groupby
from typing import Literal

from pygments.lexer import Lexer
from pygments.lexers import get_lexer_for_filename
from pygments.token import Token, _TokenType
from pygments.util import ClassNotFound

from openswe.expedited_review.eligibility import ChangedFile

logger = logging.getLogger(__name__)

LineKind = Literal["add", "remove", "context", "hunk", "meta"]


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: LineKind
    text: str
    old_number: int | None
    new_number: int | None


@dataclass(frozen=True, slots=True)
class DiffFile:
    filename: str
    additions: int
    deletions: int
    lines: list[DiffLine]

    @classmethod
    def parse(cls, file: ChangedFile) -> DiffFile:
        lines: list[DiffLine] = []
        old_number = 0
        new_number = 0
        for raw in cls._records(file.patch or ""):
            if raw.startswith("@@"):
                old_number, new_number = cls._hunk_start(raw)
                lines.append(DiffLine("hunk", raw, None, None))
                continue
            if raw.startswith("+"):
                lines.append(DiffLine("add", raw[1:], None, new_number))
                new_number += 1
                continue
            if raw.startswith("-"):
                lines.append(DiffLine("remove", raw[1:], old_number, None))
                old_number += 1
                continue
            if raw.startswith("\\"):
                lines.append(DiffLine("meta", raw, None, None))
                continue
            lines.append(
                DiffLine("context", raw[1:] if raw.startswith(" ") else raw, old_number, new_number)
            )
            old_number += 1
            new_number += 1
        return cls(file.filename, file.additions, file.deletions, lines)

    def changed_ranges(self) -> dict[int, list[tuple[int, int]]]:
        ranges: dict[int, list[tuple[int, int]]] = {}
        for changed, group in groupby(
            enumerate(self.lines), key=lambda item: item[1].kind in ("add", "remove", "meta")
        ):
            lines = [(index, line) for index, line in group if line.kind != "meta"]
            if not changed or [line.kind for _, line in lines] != ["remove", "add"]:
                continue
            if max(len(line.text) for _, line in lines) > 4096:
                continue
            tokens = [re.findall(r"\w+|\s+|[^\w\s]", line.text) for _, line in lines]
            if len(tokens[0]) * len(tokens[1]) > 250_000:
                continue
            offsets = [
                list(accumulate((len(token.replace("\t", "    ")) for token in side), initial=0))
                for side in tokens
            ]
            for tag, old_start, old_stop, new_start, new_stop in SequenceMatcher(
                None, tokens[0], tokens[1], autojunk=False
            ).get_opcodes():
                if tag == "equal":
                    continue
                for side, start, stop in ((0, old_start, old_stop), (1, new_start, new_stop)):
                    if start != stop:
                        ranges.setdefault(lines[side][0], []).append(
                            (offsets[side][start], offsets[side][stop])
                        )
        return ranges

    @staticmethod
    def _records(patch: str) -> list[str]:
        """Patch records, split on LF only.

        ``splitlines`` also breaks on form feed, vertical tab and U+2028/U+2029,
        which a patched source line may legitimately contain; splitting there
        would invent rows and desynchronise the line counters.
        """
        records = patch.split("\n")
        if records and not records[-1]:
            records.pop()
        return records

    @staticmethod
    def _hunk_start(header: str) -> tuple[int, int]:
        try:
            ranges = header.split("@@")[1].strip()
            old_part, new_part = ranges.split(" ")
            old = int(old_part.lstrip("-").split(",")[0])
            new = int(new_part.lstrip("+").split(",")[0])
        except IndexError, ValueError:
            logger.warning("Unparsable diff hunk header", extra={"hunk_header": header})
            return 1, 1
        return old, new


def lexer_for(filename: str) -> Lexer | None:
    try:
        return get_lexer_for_filename(filename, stripnl=False)
    except ClassNotFound:
        logger.debug(
            "No lexer for diff file; showing it unhighlighted", extra={"diff_file": filename}
        )
        return None


def line_tokens(lexer: Lexer | None, text: str) -> list[tuple[_TokenType, str]]:
    """``text`` split into pygments tokens that concatenate back to exactly ``text``."""
    if not text.strip() or lexer is None:
        return [(Token.Text, text)]
    tokens: list[tuple[_TokenType, str]] = []
    consumed = 0
    for token, value in lexer.get_tokens(text):
        fragment = text[consumed : consumed + len(value)]
        if fragment != value:
            break
        consumed += len(value)
        if fragment:
            tokens.append((token, fragment))
    if consumed < len(text):
        tokens.append((Token.Text, text[consumed:]))
    return tokens or [(Token.Text, text)]
