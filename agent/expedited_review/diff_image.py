"""Render a pull request's diff as a syntax-highlighted PNG for Slack."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_for_filename
from pygments.styles import get_style_by_name
from pygments.token import _TokenType
from pygments.util import ClassNotFound

from agent.expedited_review.eligibility import ChangedFile

LineKind = Literal["add", "remove", "context", "hunk", "meta"]

_FONT_DIR = Path(__file__).parent / "fonts"
_REGULAR_FONT = _FONT_DIR / "JetBrainsMonoNL-Regular.ttf"
_BOLD_FONT = _FONT_DIR / "JetBrainsMonoNL-Bold.ttf"
_ITALIC_FONT = _FONT_DIR / "JetBrainsMonoNL-Italic.ttf"
_CODE_COLUMNS = 80
_STYLE_NAME = "github-dark"


@dataclass(frozen=True, slots=True)
class DiffTheme:
    """GitHub's dark diff palette, which reads the same in either Slack theme."""

    background: str = "#0d1117"
    surface: str = "#161b22"
    border: str = "#30363d"
    text: str = "#e6edf3"
    muted: str = "#8b949e"
    add_background: str = "#12261e"
    remove_background: str = "#25171c"
    add_marker: str = "#3fb950"
    remove_marker: str = "#f85149"
    hunk_background: str = "#161b22"
    hunk_text: str = "#8b949e"
    line_number: str = "#6e7681"

    def row_background(self, kind: LineKind) -> str:
        if kind == "add":
            return self.add_background
        if kind == "remove":
            return self.remove_background
        if kind == "hunk":
            return self.hunk_background
        return self.background


@dataclass(frozen=True, slots=True)
class Metrics:
    font_size: int = 26
    line_height: int = 36
    padding: int = 18
    header_height: int = 52
    gutter_padding: int = 14


@dataclass(frozen=True, slots=True)
class FontSet:
    """The vendored JetBrains Mono faces; nothing here depends on system fonts."""

    regular: ImageFont.FreeTypeFont
    bold: ImageFont.FreeTypeFont
    italic: ImageFont.FreeTypeFont

    @classmethod
    def load(cls, size: int) -> FontSet:
        return cls(
            ImageFont.truetype(_REGULAR_FONT, size),
            ImageFont.truetype(_BOLD_FONT, size),
            ImageFont.truetype(_ITALIC_FONT, size),
        )

    def pick(self, *, bold: bool, italic: bool) -> ImageFont.FreeTypeFont:
        if bold:
            return self.bold
        if italic:
            return self.italic
        return self.regular


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
        for raw in (file.patch or "").splitlines():
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

    @staticmethod
    def _hunk_start(header: str) -> tuple[int, int]:
        try:
            ranges = header.split("@@")[1].strip()
            old_part, new_part = ranges.split(" ")
            old = int(old_part.lstrip("-").split(",")[0])
            new = int(new_part.lstrip("+").split(",")[0])
        except IndexError, ValueError:
            return 1, 1
        return old, new


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    color: str
    bold: bool = False
    italic: bool = False

    def slice(self, start: int, stop: int) -> Span:
        return Span(self.text[start:stop], self.color, bold=self.bold, italic=self.italic)


@dataclass(frozen=True, slots=True)
class Row:
    """One drawn line: a whole diff line, or one wrapped fragment of it."""

    kind: LineKind
    spans: list[Span]
    old_number: int | None
    new_number: int | None
    continuation: bool


class Highlighter:
    """Per-line pygments colouring, so a diff row never inherits a neighbour's state."""

    def __init__(self, filename: str, theme: DiffTheme) -> None:
        self._theme = theme
        self._lexer = self._pick_lexer(filename)
        self._style = get_style_by_name(_STYLE_NAME)

    @staticmethod
    def _pick_lexer(filename: str) -> Lexer | None:
        try:
            return get_lexer_for_filename(filename, stripnl=False)
        except ClassNotFound:
            return None

    def _span(self, token: _TokenType, value: str) -> Span:
        style = self._style.style_for_token(token)
        color = style.get("color")
        return Span(
            value,
            f"#{color}" if color else self._theme.text,
            bold=bool(style.get("bold")),
            italic=bool(style.get("italic")),
        )

    def spans(self, text: str) -> list[Span]:
        if not text.strip() or self._lexer is None:
            return [Span(text, self._theme.text)]
        spans: list[Span] = []
        consumed = 0
        for token, value in self._lexer.get_tokens(text):
            fragment = text[consumed : consumed + len(value)]
            if fragment != value:
                break
            consumed += len(value)
            if fragment:
                spans.append(self._span(token, fragment))
        if consumed < len(text):
            spans.append(Span(text[consumed:], self._theme.text))
        return spans or [Span(text, self._theme.text)]


class Wrapper:
    """Splits a line into exact slices of at most ``columns`` characters."""

    def __init__(self, columns: int) -> None:
        self._columns = columns

    def slices(self, text: str) -> list[str]:
        if len(text) <= self._columns:
            return [text]
        pieces: list[str] = []
        rest = text
        while len(rest) > self._columns:
            cut = self._break_point(rest)
            pieces.append(rest[:cut])
            rest = rest[cut:]
        if rest:
            pieces.append(rest)
        return pieces

    def _break_point(self, text: str) -> int:
        window = text[: self._columns]
        space = window.rfind(" ")
        if space > self._columns // 2:
            return space + 1
        return self._columns


class DiffImageRenderer:
    """Draws one PNG holding every file of a diff."""

    def __init__(self, theme: DiffTheme | None = None, metrics: Metrics | None = None) -> None:
        self._theme = theme or DiffTheme()
        self._metrics = metrics or Metrics()
        self._fonts = FontSet.load(self._metrics.font_size)
        self._char_width = self._fonts.regular.getlength("0")
        self._wrapper = Wrapper(_CODE_COLUMNS)

    def render(self, files: list[ChangedFile]) -> bytes:
        parsed = [DiffFile.parse(file) for file in files if file.patch]
        if not parsed:
            raise ValueError("no textual patches to render")

        flowed = [(file, self._flow(file)) for file in parsed]
        digits = max(
            (
                len(str(number))
                for file in parsed
                for line in file.lines
                for number in (line.old_number, line.new_number)
                if number is not None
            ),
            default=2,
        )
        gutter = int((digits * 2 + 1) * self._char_width + self._metrics.gutter_padding * 2)
        width = int(
            self._metrics.padding * 2
            + gutter
            + (_CODE_COLUMNS + 3) * self._char_width
            + self._metrics.gutter_padding
        )
        height = sum(self._file_height(rows) for _, rows in flowed) + self._metrics.padding

        image = Image.new("RGB", (width, height), self._theme.background)
        draw = ImageDraw.Draw(image)
        y = self._metrics.padding
        for file, rows in flowed:
            y = self._draw_file(draw, file, rows, y, width, gutter, digits)
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def _flow(self, file: DiffFile) -> list[Row]:
        highlighter = Highlighter(file.filename, self._theme)
        rows: list[Row] = []
        for line in file.lines:
            text = line.text.replace("\t", "    ")
            spans = (
                [Span(text, self._theme.hunk_text)]
                if line.kind in ("hunk", "meta")
                else highlighter.spans(text)
            )
            offset = 0
            for index, piece in enumerate(self._wrapper.slices(text)):
                rows.append(
                    Row(
                        line.kind,
                        self._spans_between(spans, offset, offset + len(piece)),
                        line.old_number if index == 0 else None,
                        line.new_number if index == 0 else None,
                        continuation=index > 0,
                    )
                )
                offset += len(piece)
        return rows

    @staticmethod
    def _spans_between(spans: list[Span], start: int, stop: int) -> list[Span]:
        out: list[Span] = []
        cursor = 0
        for span in spans:
            span_start, span_end = cursor, cursor + len(span.text)
            cursor = span_end
            if span_end <= start or span_start >= stop:
                continue
            out.append(span.slice(max(start - span_start, 0), min(stop, span_end) - span_start))
        return out

    def _file_height(self, rows: list[Row]) -> int:
        return self._metrics.header_height + len(rows) * self._metrics.line_height + 14

    def _draw_file(
        self,
        draw: ImageDraw.ImageDraw,
        file: DiffFile,
        rows: list[Row],
        top: int,
        width: int,
        gutter: int,
        digits: int,
    ) -> int:
        theme = self._theme
        metrics = self._metrics
        left = metrics.padding
        right = width - metrics.padding
        header_bottom = top + metrics.header_height
        baseline = (metrics.header_height - metrics.font_size) // 2

        draw.rectangle((left, top, right, header_bottom), fill=theme.surface)
        draw.text(
            (left + metrics.gutter_padding, top + baseline),
            file.filename,
            font=self._fonts.bold,
            fill=theme.text,
        )
        counts = f"+{file.additions}  \u2212{file.deletions}"
        draw.text(
            (right - metrics.gutter_padding - self._fonts.bold.getlength(counts), top + baseline),
            counts,
            font=self._fonts.bold,
            fill=theme.muted,
        )

        y = header_bottom
        for row in rows:
            self._draw_row(draw, row, y, left, right, gutter, digits)
            y += metrics.line_height

        draw.line((left + gutter, header_bottom, left + gutter, y), fill=theme.border, width=1)
        draw.rectangle((left, top, right, y), outline=theme.border, width=1)
        return y + 14

    def _draw_row(
        self,
        draw: ImageDraw.ImageDraw,
        row: Row,
        y: int,
        left: int,
        right: int,
        gutter: int,
        digits: int,
    ) -> None:
        theme = self._theme
        metrics = self._metrics
        draw.rectangle(
            (left, y, right, y + metrics.line_height), fill=theme.row_background(row.kind)
        )
        text_y = y + (metrics.line_height - metrics.font_size) // 2
        marker_x = left + gutter + int(self._char_width)

        if not row.continuation and row.kind not in ("hunk", "meta"):
            numbers = f"{row.old_number or '':>{digits}} {row.new_number or '':>{digits}}"
            draw.text(
                (left + metrics.gutter_padding, text_y),
                numbers,
                font=self._fonts.regular,
                fill=theme.line_number,
            )

        if row.kind in ("add", "remove"):
            marker = "+" if row.kind == "add" else "\u2212"
            color = theme.add_marker if row.kind == "add" else theme.remove_marker
            draw.text(
                (marker_x, text_y),
                marker if not row.continuation else "\u00b7",
                font=self._fonts.bold,
                fill=color if not row.continuation else theme.line_number,
            )
        elif row.continuation:
            draw.text(
                (marker_x, text_y), "\u00b7", font=self._fonts.regular, fill=theme.line_number
            )

        x = marker_x + self._char_width * 2
        for span in row.spans:
            font = self._fonts.pick(bold=span.bold, italic=span.italic)
            draw.text((x, text_y), span.text, font=font, fill=span.color)
            x += font.getlength(span.text)


def render_diff_png(files: list[ChangedFile]) -> bytes:
    return DiffImageRenderer().render(files)
