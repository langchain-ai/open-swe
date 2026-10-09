from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image, ImageColor, ImageDraw

from openswe.expedited_review.diff_image import (
    DiffFile,
    DiffImageRenderer,
    DiffTheme,
    Highlighter,
    Span,
    render_diff_png,
)
from openswe.expedited_review.eligibility import ChangedFile

PATCH = """@@ -10,4 +10,4 @@ def existing() -> None:
 keep = 1
-was = "old"
+now = "new"
 tail = 2
"""


def test_wrapped_rows_carry_the_whole_highlighted_line() -> None:
    long_line = "+    value = " + " + ".join(f'"chunk{index}"' for index in range(20))
    patch = f"@@ -1,1 +1,2 @@\n context = 0\n{long_line}\n"
    rows = DiffImageRenderer()._flow(
        DiffFile.parse(ChangedFile(filename="x.py", additions=1, deletions=0, patch=patch))
    )
    added = [row for row in rows if row.kind == "add"]

    assert len(added) > 1
    assert [row.continuation for row in added] == [False] + [True] * (len(added) - 1)
    assert "".join(span.text for row in added for span in row.spans) == long_line[1:]


def test_unicode_separators_stay_inside_their_line() -> None:
    patch = '@@ -1,1 +1,1 @@\n-old = 1\n+new = "a\u2028b"\n'
    parsed = DiffFile.parse(ChangedFile(filename="x.py", additions=1, deletions=1, patch=patch))

    assert [(line.kind, line.text) for line in parsed.lines] == [
        ("hunk", "@@ -1,1 +1,1 @@"),
        ("remove", "old = 1"),
        ("add", 'new = "a\u2028b"'),
    ]


def test_render_produces_a_png_covering_both_files() -> None:
    png = render_diff_png(
        [
            ChangedFile(filename="x.py", additions=1, deletions=1, patch=PATCH),
            ChangedFile(filename="README.md", additions=1, deletions=1, patch=PATCH),
        ]
    )
    single = render_diff_png([ChangedFile(filename="x.py", additions=1, deletions=1, patch=PATCH)])

    with Image.open(BytesIO(png)) as image, Image.open(BytesIO(single)) as one_file:
        assert image.format == "PNG"
        assert image.width == one_file.width
        assert image.height > one_file.height


@pytest.mark.parametrize(
    ("old", "new", "removed", "added"),
    [
        ('value = "old"', 'value = "new"', "old", "new"),
        ("foo_bar()", "foo_baz()", "foo_bar", "foo_baz"),
        ("x == 1", "x != 1", "=", "!"),
        ("x = 1", "x  = 1", " ", "  "),
        ("\tvalue = 1", "    value = 1", "    ", "    "),
        ("a a a b", "a a a c", "b", "c"),
        ('value = "café\u2028old"', 'value = "café\u2028new"', "old", "new"),
        ("call()", "call(value)", "", "value"),
        ("call(value)", "call()", "value", ""),
        ('value = "' + "old" * 40 + '"', 'value = "' + "new" * 40 + '"', "old" * 40, "new" * 40),
    ],
)
def test_word_highlights_preserve_text_and_syntax(
    old: str, new: str, removed: str, added: str
) -> None:
    file = ChangedFile(
        filename="x.py", additions=1, deletions=1, patch=f"@@ -1 +1 @@\n-{old}\n+{new}"
    )
    rows = DiffImageRenderer()._flow(DiffFile.parse(file))
    for kind, text, expected in (("remove", old, removed), ("add", new, added)):
        selected = [row for row in rows if row.kind == kind]
        spans = [span for row in selected for span in row.spans]
        displayed = text.replace("\t", "    ")
        assert "".join(span.text for span in spans) == displayed
        assert "".join(span.text for span in spans if span.changed) == expected
        assert [(span.color, span.bold, span.italic) for span in spans for _ in span.text] == [
            (span.color, span.bold, span.italic)
            for span in Highlighter("x.py", DiffTheme()).spans(displayed)
            for _ in span.text
        ]
        assert selected[0].old_number == (1 if kind == "remove" else None)
        assert selected[0].new_number == (1 if kind == "add" else None)
        assert all(row.old_number is None and row.new_number is None for row in selected[1:])


@pytest.mark.parametrize(
    "body",
    [
        "-old\n+new\n+extra",
        "-old\n-removed\n+new",
        "-old\n context\n+new",
        "-old\n@@ -5 +5 @@\n+new",
        "+new",
        "-old",
        "-" + "x" * 4097 + "\n+" + "y" * 4097,
        "-" + "x " * 300 + "\n+" + "y " * 300,
    ],
)
def test_unpaired_or_over_budget_lines_keep_whole_line_rendering(body: str) -> None:
    parsed = DiffFile.parse(
        ChangedFile(filename="x.py", additions=1, deletions=1, patch="@@ -1 +1 @@\n" + body)
    )
    assert parsed.changed_ranges() == {}
    rows = DiffImageRenderer()._flow(parsed)
    for kind in ("add", "remove"):
        assert "".join(
            span.text for row in rows if row.kind == kind for span in row.spans
        ) == "".join(line.text for line in parsed.lines if line.kind == kind)
    assert not any(span.changed for row in rows for span in row.spans)


def test_no_newline_markers_do_not_prevent_pairing() -> None:
    parsed = DiffFile.parse(
        ChangedFile(
            filename="x.py",
            additions=1,
            deletions=1,
            patch="@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file",
        )
    )
    assert parsed.changed_ranges() == {1: [(0, 3)], 3: [(0, 3)]}


@pytest.mark.parametrize("character", ["\u200b", "\u200d"])
def test_zero_width_changes_still_render(character: str) -> None:
    file = ChangedFile(
        filename="x.py",
        additions=1,
        deletions=1,
        patch=f'@@ -1 +1 @@\n-x = "a"\n+x = "a{character}"',
    )
    with Image.open(BytesIO(render_diff_png([file]))) as image:
        assert image.format == "PNG"


@pytest.mark.parametrize("suffix", ["\u0301", "\u0300", "\u0301\u0327"])
def test_highlight_boundaries_preserve_combining_mark_glyphs(suffix: str) -> None:
    theme = DiffTheme(add_highlight="#12261e", remove_highlight="#25171c")
    renderer = DiffImageRenderer(theme)
    file = ChangedFile(
        filename="example.unknown",
        additions=1,
        deletions=1,
        patch=f"@@ -1 +1 @@\n-cafe\u0302 tail\n+cafe{suffix} tail",
    )
    for row in renderer._flow(DiffFile.parse(file)):
        if row.kind not in ("add", "remove"):
            continue
        assert any(span.changed for span in row.spans)
        text = "".join(span.text for span in row.spans)
        whole = replace(row, spans=[Span(text, theme.text)])
        actual = Image.new("RGB", (600, 36), theme.background)
        expected = Image.new("RGB", actual.size, theme.background)
        renderer._draw_row(ImageDraw.Draw(actual), row, 0, 0, 599, 60, 1)
        renderer._draw_row(ImageDraw.Draw(expected), whole, 0, 0, 599, 60, 1)
        assert actual.tobytes() == expected.tobytes()


def test_png_paints_intraline_backgrounds_only_for_replacements() -> None:
    theme = DiffTheme()
    paired = ChangedFile(filename="x.py", additions=1, deletions=1, patch=PATCH)
    added = ChangedFile(filename="x.py", additions=1, deletions=0, patch="@@ -0,0 +1 @@\n+x = 1")
    with (
        Image.open(BytesIO(render_diff_png([paired]))) as image,
        Image.open(BytesIO(render_diff_png([added]))) as addition,
    ):
        colors = image.getcolors(image.width * image.height)
        addition_colors = addition.getcolors(addition.width * addition.height)
        assert colors is not None and addition_colors is not None
        for color in (theme.add_highlight, theme.remove_highlight):
            assert any(rgb == ImageColor.getrgb(color) and count > 100 for count, rgb in colors)
            assert all(rgb != ImageColor.getrgb(color) for _, rgb in addition_colors)
