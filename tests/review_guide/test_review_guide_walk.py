from openswe.review_guide.walk import Reader, Walk
from openswe.sandbox_scripts.walkthrough import parse_index
from openswe.walkthrough.diff import Carry, DiffIndex, LineMaps
from openswe.walkthrough.lines import LineSet
from openswe.walkthrough.plan import Plan, PlanChunk


def _new_file(path: str, *lines: str) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{body}"
    )


def _binary(path: str, blob: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\nindex {'0' * 40}..{blob * 40} 100644\n"
        f"Binary files a/{path} and b/{path} differ\n"
    )


def _index(diff: str) -> DiffIndex:
    return DiffIndex.model_validate({"files": parse_index(diff.splitlines())})


def _added(path: str, *linenos: int) -> LineSet:
    return LineSet.of(path, "+", [(n, n) for n in linenos])


def _plan() -> Plan:
    plan = Plan.start("head-1", _index(_new_file("a.py", "import os", "def one():", "def two():")))
    plan.add_other(_added("a.py", 1))
    plan.add_chunk(PlanChunk(id="one", title="One", lines=_added("a.py", 2)))
    plan.add_chunk(PlanChunk(id="two", title="Two", lines=_added("a.py", 3)))
    return plan


def test_only_looks_good_counts_and_the_walk_finishes_once_everything_is_settled() -> None:
    plan = _plan()
    walk = Walk(head_sha="head-1")
    seen = LineSet()
    nothing = LineSet()

    reader = Reader.of(walk, plan, seen)
    walk.on_screen = reader.show(plan.chunks[0]).model_copy(update={"message_ts": "1.0"})
    reader = Reader.of(walk, plan, seen)
    assert reader.unfinished(nothing) == [
        "2 chunks are not yet approved or skipped",
        "“One” is on screen and not yet approved",
        "Other has not been approved or skipped",
    ]
    assert reader.next_chunk() is plan.chunks[1]

    walk.approve("1.0")
    walk.skip("reads tests later", Reader.of(walk, plan, seen).remaining(plan.chunks[1].lines))
    reader = Reader.of(walk, plan, seen)
    assert reader.next_chunk() is None
    assert reader.unfinished(nothing) == ["Other has not been approved or skipped"]

    walk.on_screen = reader.show_other().model_copy(update={"message_ts": "2.0"})
    walk.approve("2.0")
    assert Reader.of(walk, plan, seen).unfinished(nothing) == []
    assert walk.coverage() == (
        "Walked through 1 chunks (1 changed lines); skipped 1 lines at the reader's request: "
        "“reads tests later”; Other, 1 lines, summarized."
    )


def test_lines_seen_in_another_channel_count_as_reviewed() -> None:
    plan = _plan()

    reader = Reader.of(Walk(head_sha="head-1"), plan, _added("a.py", 2))

    assert reader.next_chunk() is plan.chunks[1]
    assert not reader.remaining(plan.chunks[0].lines)


def test_a_push_that_changes_a_binary_file_reopens_an_approved_other() -> None:
    plan = Plan.start("head-1", _index(_binary("logo.png", "a")))
    walk = Walk(head_sha="head-1")
    walk.on_screen = Reader.of(walk, plan, LineSet()).show_other()
    walk.on_screen.message_ts = "1.0"
    walk.approve("1.0")

    def pending(diff: str) -> bool:
        index = _index(diff)
        carried = plan.carried_to("head-2", index, Carry(LineMaps(), index))
        return Reader.of(walk, carried, LineSet()).other_pending

    assert not pending(_binary("logo.png", "a"))
    assert pending(_binary("logo.png", "b"))
    assert pending(_binary("logo.png", "a") + _binary("new.png", "c"))


def test_a_push_keeps_an_unchanged_chunk_on_screen_and_takes_down_a_changed_one() -> None:
    plan = _plan()
    walk = Walk(head_sha="head-1")
    walk.on_screen = Reader.of(walk, plan, LineSet()).show(plan.chunks[0])

    shifted = _index(_new_file("a.py", "import os", "", "def one():", "def two():"))
    # A blank line inserted after line 1.
    kept, gone = walk.moved_to(
        "head-2", "base", Carry(LineMaps(added={"a.py": [(1, 0, 2, 1)]}), shifted)
    )
    edited = _index(_new_file("a.py", "import os", "def one(x):", "def two():"))
    # Line 2 rewritten.
    dropped, taken_down = walk.moved_to(
        "head-3", "base", Carry(LineMaps(added={"a.py": [(2, 1, 2, 1)]}), edited)
    )

    assert gone is None
    assert kept.on_screen is not None
    assert kept.on_screen.lines == _added("a.py", 3)
    assert dropped.on_screen is None
    assert taken_down is not None and taken_down.title == "One"
