"""Render a pull request's diff as a self-contained, interactive HTML page for Slack."""

from dataclasses import dataclass
from html import escape

from pygments.formatters.html import HtmlFormatter
from pygments.lexer import Lexer
from pygments.token import STANDARD_TYPES, _TokenType

from openswe.expedited_review.diff_image import DiffFile, DiffLine, lexer_for, line_tokens
from openswe.expedited_review.eligibility import ChangedFile

_LIGHT_STYLE = "default"
_DARK_STYLE = "github-dark"
_CODE_SCOPE = ".code"

_PAGE_CSS = """
:root{color-scheme:light;--bg:#f6f8fa;--panel:#fff;--text:#1f2328;--muted:#59636e;
--line:#d1d9e0;--accent:#0969da;--add:#dafbe1;--add-mark:#aceebb;--remove:#ffebe9;
--remove-mark:#ffcecb;--hunk:#ddf4ff;--add-sign:#1a7f37;--remove-sign:#cf222e}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
--bg:#0d1117;--panel:#151b23;--text:#e6edf3;--muted:#9198a1;--line:#3d444d;--accent:#4493f8;
--add:#12261e;--add-mark:#245b35;--remove:#25171c;--remove-mark:#743039;--hunk:#121d2f;
--add-sign:#3fb950;--remove-sign:#f85149}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0d1117;--panel:#151b23;--text:#e6edf3;
--muted:#9198a1;--line:#3d444d;--accent:#4493f8;--add:#12261e;--add-mark:#245b35;
--remove:#25171c;--remove-mark:#743039;--hunk:#121d2f;--add-sign:#3fb950;--remove-sign:#f85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
header{position:sticky;top:0;z-index:2;display:grid;gap:8px;padding:12px 16px;
background:var(--panel);border-bottom:1px solid var(--line)}
h1{margin:0;font-size:16px;text-wrap:balance}
.meta,.stat{color:var(--muted);font-variant-numeric:tabular-nums}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button,input{font:inherit;color:var(--text);background:var(--bg);border:1px solid var(--line);
border-radius:6px;padding:4px 10px}
button{cursor:pointer}button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}
input{min-width:200px;flex:1}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.layout{display:grid;grid-template-columns:minmax(180px,260px) 1fr;gap:16px;padding:16px}
nav{position:sticky;top:120px;align-self:start;display:grid;gap:2px;max-height:80vh;overflow:auto}
nav button{display:flex;justify-content:space-between;gap:8px;border:0;background:none;
text-align:left;overflow-wrap:anywhere;padding:4px 6px}
nav button:hover{background:var(--panel)}
main{display:grid;gap:16px;min-width:0}
details{background:var(--panel);border:1px solid var(--line);border-radius:6px;overflow:hidden}
summary{display:flex;justify-content:space-between;gap:12px;padding:8px 12px;cursor:pointer;
font:600 13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;
font:12px/20px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
td{padding:0 8px;vertical-align:top}
.num{width:1%;min-width:40px;text-align:right;color:var(--muted);user-select:none}
.sign{width:1%;user-select:none}
.code{white-space:pre}body.wrap .code{white-space:pre-wrap;overflow-wrap:anywhere}
tr.add{background:var(--add)}tr.add .sign{color:var(--add-sign)}
tr.remove{background:var(--remove)}tr.remove .sign{color:var(--remove-sign)}
tr.hunk,tr.meta{background:var(--hunk);color:var(--muted)}
tr.add mark{background:var(--add-mark);color:inherit}
tr.remove mark{background:var(--remove-mark);color:inherit}
body.changes-only tr.context{display:none}
.hidden{display:none}
@media(max-width:720px){.layout{grid-template-columns:1fr}nav{position:static;max-height:none}}
@media(prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
"""

_PAGE_JS = """
const files=[...document.querySelectorAll("main details")];
const pressed=(button,on)=>button.setAttribute("aria-pressed",String(on));
document.getElementById("expand").onclick=()=>files.forEach(f=>f.open=true);
document.getElementById("collapse").onclick=()=>files.forEach(f=>f.open=false);
for(const [id,cls] of [["wrap","wrap"],["changes","changes-only"]]){
  const button=document.getElementById(id);
  button.onclick=()=>pressed(button,document.body.classList.toggle(cls));
}
document.getElementById("filter").oninput=event=>{
  const query=event.target.value.toLowerCase();
  document.querySelectorAll("[data-path]").forEach(el=>
    el.classList.toggle("hidden",!el.dataset.path.toLowerCase().includes(query)));
};
document.querySelectorAll("nav button").forEach(button=>button.onclick=()=>{
  const file=document.getElementById(button.dataset.target);
  file.open=true;file.scrollIntoView({block:"start"});
});
"""


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
class _Segment:
    text: str
    css_class: str
    changed: bool


class DiffHtmlRenderer:
    """One page holding every file of a diff, with navigation, filtering and wrapping."""

    def __init__(self, *, label: str, title: str, url: str) -> None:
        self._label = label
        self._title = title
        self._url = url

    def render(self, files: list[ChangedFile]) -> bytes:
        parsed = [DiffFile.parse(file) for file in files if file.patch]
        if not parsed:
            raise ValueError("no textual patches to render")
        additions = sum(file.additions for file in parsed)
        deletions = sum(file.deletions for file in parsed)
        nav = "".join(
            f'<button type="button" data-target="file-{index}" data-path="{escape(file.filename)}">'
            f'<span>{escape(file.filename)}</span><span class="stat">+{file.additions} '
            f"\u2212{file.deletions}</span></button>"
            for index, file in enumerate(parsed)
        )
        body = "".join(self._file(index, file) for index, file in enumerate(parsed))
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Diff of {escape(self._label)}</title>"
            f"<style>{_PAGE_CSS}{_token_css()}</style></head><body>"
            f"<header><h1>{escape(self._label)} {escape(self._title)}</h1>"
            f'<div class="meta">{len(parsed)} {"file" if len(parsed) == 1 else "files"} · '
            f"+{additions} \u2212{deletions} · {escape(self._url)}</div>"
            '<div class="toolbar"><input id="filter" type="search" placeholder="Filter files" '
            'aria-label="Filter files">'
            '<button id="expand" type="button">Expand all</button>'
            '<button id="collapse" type="button">Collapse all</button>'
            '<button id="wrap" type="button" aria-pressed="false">Wrap lines</button>'
            '<button id="changes" type="button" aria-pressed="false">Changes only</button>'
            "</div></header>"
            f'<div class="layout"><nav aria-label="Changed files">{nav}</nav>'
            f"<main>{body}</main></div><script>{_PAGE_JS}</script></body></html>"
        ).encode()

    def _file(self, index: int, file: DiffFile) -> str:
        lexer = lexer_for(file.filename)
        changes = file.changed_ranges()
        rows = "".join(
            self._row(line, lexer, changes.get(line_index, []))
            for line_index, line in enumerate(file.lines)
        )
        return (
            f'<details id="file-{index}" data-path="{escape(file.filename)}" open>'
            f"<summary><span>{escape(file.filename)}</span>"
            f'<span class="stat">+{file.additions} \u2212{file.deletions}</span></summary>'
            f'<div class="scroll"><table>{rows}</table></div></details>'
        )

    def _row(self, line: DiffLine, lexer: Lexer | None, changes: list[tuple[int, int]]) -> str:
        if line.kind in ("hunk", "meta"):
            return (
                f'<tr class="{line.kind}"><td class="num"></td><td class="num"></td>'
                f'<td class="sign"></td><td class="code">{escape(line.text)}</td></tr>'
            )
        sign = {"add": "+", "remove": "\u2212"}.get(line.kind, "")
        code = "".join(
            self._segment_html(segment) for segment in self._segments(line, lexer, changes)
        )
        return (
            f'<tr class="{line.kind}"><td class="num">{line.old_number or ""}</td>'
            f'<td class="num">{line.new_number or ""}</td><td class="sign">{sign}</td>'
            f'<td class="code">{code}</td></tr>'
        )

    @staticmethod
    def _segments(
        line: DiffLine, lexer: Lexer | None, changes: list[tuple[int, int]]
    ) -> list[_Segment]:
        """Highlighted tokens, split wherever an intra-line change starts or stops."""
        text = line.text.replace("\t", "    ")
        cuts = sorted({0, len(text), *(edge for change in changes for edge in change)})
        segments: list[_Segment] = []
        offset = 0
        for token, value in line_tokens(lexer, text):
            stop = offset + len(value)
            edges = [offset, *(cut for cut in cuts if offset < cut < stop), stop]
            for start, end in zip(edges, edges[1:], strict=False):
                changed = any(low <= start and end <= high for low, high in changes)
                segments.append(_Segment(text[start:end], _css_class(token), changed))
            offset = stop
        return segments

    @staticmethod
    def _segment_html(segment: _Segment) -> str:
        inner = escape(segment.text)
        if segment.css_class:
            inner = f'<span class="{segment.css_class}">{inner}</span>'
        return f"<mark>{inner}</mark>" if segment.changed else inner


def render_diff_html(files: list[ChangedFile], *, label: str, title: str, url: str) -> bytes:
    return DiffHtmlRenderer(label=label, title=title, url=url).render(files)
