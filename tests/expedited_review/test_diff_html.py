import pytest

from openswe.expedited_review.diff_html import FileView, render_diff_html
from openswe.expedited_review.diff_lines import DiffFile, lexer_for, line_tokens
from openswe.expedited_review.eligibility import ChangedFile


def test_unicode_separators_stay_inside_their_line() -> None:
    patch = '@@ -1,1 +1,1 @@\n-old = 1\n+new = "a\u2028b"\n'
    parsed = DiffFile.parse(ChangedFile(filename="x.py", additions=1, deletions=1, patch=patch))

    assert [(line.kind, line.text) for line in parsed.lines] == [
        ("hunk", "@@ -1,1 +1,1 @@"),
        ("remove", "old = 1"),
        ("add", 'new = "a\u2028b"'),
    ]


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
    rows = FileView.of(DiffFile.parse(file)).rows
    for kind, text, expected in (("remove", old, removed), ("add", new, added)):
        [row] = [row for row in rows if row.kind == kind]
        displayed = text.replace("\t", "    ")
        assert "".join(segment.text for segment in row.segments) == displayed
        assert "".join(segment.text for segment in row.segments if segment.changed) == expected
        assert len({segment.css_class for segment in row.segments}) > 1 or not displayed.strip()
        assert (row.old_number, row.new_number) == ((1, None) if kind == "remove" else (None, 1))


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
def test_unpaired_or_over_budget_lines_mark_nothing(body: str) -> None:
    parsed = DiffFile.parse(
        ChangedFile(filename="x.py", additions=1, deletions=1, patch="@@ -1 +1 @@\n" + body)
    )
    assert parsed.changed_ranges() == {}
    assert not any(segment.changed for row in FileView.of(parsed).rows for segment in row.segments)


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


def test_line_tokens_concatenate_back_to_the_line() -> None:
    text = 'value = "a\u2028b"  # note'
    assert "".join(value for _, value in line_tokens(lexer_for("x.py"), text)) == text


def test_html_page_escapes_patch_and_path_content() -> None:
    hostile = "@@ -1,1 +1,1 @@\n-a = 1\n+b = '</script><img src=x onerror=alert(1)>'\n"
    page = render_diff_html(
        [ChangedFile(filename="<x>.py", additions=1, deletions=1, patch=hostile)],
        label="o/r#1",
        title="<b>title</b>",
        url="https://github.com/o/r/pull/1",
    ).decode()

    assert "<img" not in page
    assert "<b>title" not in page
    assert "&lt;x&gt;.py" in page
    assert page.count("<script>") == 1
