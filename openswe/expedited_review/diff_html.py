"""Render a pull request's diff as a self-contained, interactive HTML page for Slack."""

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pygments.formatters.html import HtmlFormatter
from pygments.lexer import Lexer
from pygments.token import STANDARD_TYPES, _TokenType

from openswe.expedited_review.diff_lines import DiffFile, DiffLine, lexer_for, line_tokens
from openswe.expedited_review.eligibility import ChangedFile

_LIGHT_STYLE = "default"
_DARK_STYLE = "github-dark"
_CODE_SCOPE = ".code"
_ASSETS = Path(__file__).parent / "diff_page"
_SIGNS = {"add": "+", "remove": "\u2212"}

_TEMPLATES = Environment(
    loader=FileSystemLoader(_ASSETS),
    autoescape=True,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _token_rules(style: str, scope: str) -> str:
    defs = HtmlFormatter(style=style).get_token_style_defs(scope)
    return "\n".join(line for line in defs if ".hll" not in line)


def _token_css() -> str:
    """Pygments colours for both themes, following the same layers as the page palette."""
    system_dark = _token_rules(_DARK_STYLE, f':root:not([data-theme="light"]) {_CODE_SCOPE}')
    return "\n".join(
        [
            _token_rules(_LIGHT_STYLE, _CODE_SCOPE),
            f"@media(prefers-color-scheme:dark){{{system_dark}}}",
            _token_rules(_DARK_STYLE, f':root[data-theme="dark"] {_CODE_SCOPE}'),
        ]
    )


def _css_class(token: _TokenType) -> str:
    while token not in STANDARD_TYPES and token.parent is not None:
        token = token.parent
    return STANDARD_TYPES.get(token, "")


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    css_class: str
    changed: bool


@dataclass(frozen=True, slots=True)
class Row:
    kind: str
    old_number: int | None
    new_number: int | None
    sign: str
    segments: list[Segment]

    @classmethod
    def of(cls, line: DiffLine, lexer: Lexer | None, changes: list[tuple[int, int]]) -> Row:
        if line.kind in ("hunk", "meta"):
            return cls(line.kind, None, None, "", [Segment(line.text, "", False)])
        return cls(
            line.kind,
            line.old_number,
            line.new_number,
            _SIGNS.get(line.kind, ""),
            cls._segments(line.text.replace("\t", "    "), lexer, changes),
        )

    @staticmethod
    def _segments(text: str, lexer: Lexer | None, changes: list[tuple[int, int]]) -> list[Segment]:
        """Highlighted tokens, split wherever an intra-line change starts or stops."""
        cuts = sorted({edge for change in changes for edge in change})
        segments: list[Segment] = []
        offset = 0
        for token, value in line_tokens(lexer, text):
            stop = offset + len(value)
            edges = [offset, *(cut for cut in cuts if offset < cut < stop), stop]
            for start, end in zip(edges, edges[1:], strict=False):
                changed = any(low <= start and end <= high for low, high in changes)
                segments.append(Segment(text[start:end], _css_class(token), changed))
            offset = stop
        return segments


@dataclass(frozen=True, slots=True)
class FileView:
    filename: str
    additions: int
    deletions: int
    rows: list[Row]

    @classmethod
    def of(cls, file: DiffFile) -> FileView:
        lexer = lexer_for(file.filename)
        changes = file.changed_ranges()
        rows = [
            Row.of(line, lexer, changes.get(index, [])) for index, line in enumerate(file.lines)
        ]
        return cls(file.filename, file.additions, file.deletions, rows)


def render_diff_html(files: list[ChangedFile], *, label: str, title: str, url: str) -> bytes:
    """One page holding every file of a diff, with navigation, filtering and wrapping."""
    views = [FileView.of(DiffFile.parse(file)) for file in files if file.patch]
    if not views:
        raise ValueError("no textual patches to render")
    return (
        _TEMPLATES.get_template("page.html.jinja")
        .render(
            label=label,
            title=title,
            url=url,
            files=views,
            additions=sum(view.additions for view in views),
            deletions=sum(view.deletions for view in views),
            css=(_ASSETS / "page.css").read_text(),
            js=(_ASSETS / "page.js").read_text(),
            token_css=_token_css(),
        )
        .encode()
    )
