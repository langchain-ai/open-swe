from io import BytesIO

from PIL import Image

from agent.expedited_review.diff_image import (
    _CODE_COLUMNS,
    DiffFile,
    DiffImageRenderer,
    Wrapper,
    render_diff_png,
)
from agent.expedited_review.eligibility import ChangedFile

PATCH = """@@ -10,4 +10,4 @@ def existing() -> None:
 keep = 1
-was = "old"
+now = "new"
 tail = 2
"""


def test_line_numbers_follow_the_hunk_header() -> None:
    parsed = DiffFile.parse(ChangedFile(filename="x.py", additions=1, deletions=1, patch=PATCH))
    numbered = [(line.kind, line.old_number, line.new_number) for line in parsed.lines]

    assert numbered == [
        ("hunk", None, None),
        ("context", 10, 10),
        ("remove", 11, None),
        ("add", None, 11),
        ("context", 12, 12),
    ]


def test_wrapping_keeps_every_character() -> None:
    line = " ".join(f"word{index}" for index in range(40))
    pieces = Wrapper(_CODE_COLUMNS).slices(line)

    assert len(pieces) > 1
    assert all(len(piece) <= _CODE_COLUMNS for piece in pieces)
    assert "".join(pieces) == line


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
