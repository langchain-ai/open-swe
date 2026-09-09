import pytest

from agent.tools.show_in_diff import _show_in_diff

DIFF = """\
diff --git a/agent/server.py b/agent/server.py
index 1111111..2222222 100644
--- a/agent/server.py
+++ b/agent/server.py
@@ -10,6 +10,7 @@ def build_tools():
     schedule_thread_wakeup,
     manage_code_channel,
     slack_add_reaction,
+    show_in_diff,
     slack_attach_html,
     slack_move_thread,
     slack_thread_reply,
@@ -40,7 +41,6 @@ def other():
     kept,
-    dropped,
     also_kept,
"""


def seeded(diff: str = DIFF) -> dict[str, object]:
    return {"files": {"/pr/diff.patch": {"content": diff, "encoding": "utf-8"}}}


async def show(**kwargs: object) -> tuple[str, dict]:
    return await _show_in_diff(state=seeded(), **kwargs)  # type: ignore[arg-type]


async def test_reports_the_hunk_around_a_new_side_line() -> None:
    content, artifact = await show(path="agent/server.py", line=13)
    assert artifact == {
        "type": "show_in_diff",
        "path": "agent/server.py",
        "line": 13,
        "side": "new",
    }
    marked = [line for line in content.splitlines() if line.startswith(">")]
    assert len(marked) == 1
    assert "show_in_diff," in marked[0]
    # Both gutters, as the diff view shows them.
    assert "    10     10" in content


async def test_finds_a_deleted_line_on_the_old_side() -> None:
    content, _ = await show(path="agent/server.py", line=41, side="old")
    marked = [line for line in content.splitlines() if line.startswith(">")]
    assert len(marked) == 1
    assert "dropped," in marked[0]


async def test_the_two_sides_number_the_same_row_differently() -> None:
    # Old 41 is the deleted line; new 41 is the context row above it.
    content, _ = await show(path="agent/server.py", line=41)
    marked = [line for line in content.splitlines() if line.startswith(">")]
    assert len(marked) == 1
    assert "kept," in marked[0]


async def test_says_when_the_line_is_outside_every_hunk() -> None:
    content, _ = await show(path="agent/server.py", line=999)
    assert "Line 999 is not in the diff for agent/server.py" in content
    assert "New-side ranges it covers: 10-16, 41-46" in content


async def test_says_when_the_file_is_not_in_the_diff() -> None:
    content, artifact = await show(path="agent/other.py", line=3)
    assert content == "agent/other.py is not in the diff, so the view did not move."
    assert artifact["path"] == "agent/other.py"


async def test_normalizes_the_path_and_rejects_an_empty_one() -> None:
    _, artifact = await show(path="./agent/server.py")
    assert artifact["path"] == "agent/server.py"
    with pytest.raises(ValueError, match="repository-relative"):
        await show(path="   ")
    with pytest.raises(ValueError, match="positive line number"):
        await show(path="agent/server.py", line=0)


async def test_reports_an_unreadable_diff_rather_than_a_bare_confirmation() -> None:
    content, _ = await _show_in_diff(path="agent/server.py", line=13, state={})
    assert "could not be read" in content
