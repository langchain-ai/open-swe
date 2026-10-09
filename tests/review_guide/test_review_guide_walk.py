from collections import Counter

from openswe.review_guide.walk import Reader, Walk
from openswe.walkthrough.diff import FileChange, line_key, parse
from openswe.walkthrough.plan import LineRef, Plan, PlanChunk


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


def _refs(changes: list[FileChange], *linenos: int) -> list[LineRef]:
    return [LineRef.of(line) for c in changes for line in c.lines if line.lineno in linenos]


def _plan(changes: list[FileChange]) -> Plan:
    plan = Plan.start("head-1", changes)
    plan.add_other(_refs(changes, 1))
    plan.add_chunk(PlanChunk(id="one", title="One", lines=_refs(changes, 2)))
    plan.add_chunk(PlanChunk(id="two", title="Two", lines=_refs(changes, 3)))
    return plan


def _key(ref: LineRef) -> str:
    return line_key(ref.path, ref.sign, ref.text)


def test_only_looks_good_counts_and_the_walk_finishes_once_everything_is_settled() -> None:
    changes = parse(_new_file("a.py", "import os", "def one():", "def two():"))
    plan = _plan(changes)
    walk = Walk(head_sha="head-1")
    seen: Counter[str] = Counter()

    reader = Reader.of(walk, plan, seen)
    walk.on_screen = reader.show(plan.chunks[0]).model_copy(update={"message_ts": "1.0"})
    reader = Reader.of(walk, plan, seen)
    assert reader.unfinished([]) == [
        "2 chunks are not yet approved or skipped",
        "“One” is on screen and not yet approved",
        "Other has not been approved or skipped",
    ]
    assert reader.next_chunk() is plan.chunks[1]

    walk.approve("1.0")
    seen.update(_key(r) for r in plan.chunks[0].lines)
    walk.skip("reads tests later", Reader.of(walk, plan, seen).remaining(plan.chunks[1].lines))
    reader = Reader.of(walk, plan, seen)
    assert reader.next_chunk() is None
    assert reader.unfinished([]) == ["Other has not been approved or skipped"]

    walk.on_screen = reader.show_other().model_copy(update={"message_ts": "2.0"})
    walk.approve("2.0")
    seen.update(_key(r) for r in plan.other)
    assert Reader.of(walk, plan, seen).unfinished([]) == []
    assert walk.coverage() == (
        "Walked through 1 chunks (1 changed lines); skipped 1 lines at the reader's request: "
        "“reads tests later”; Other, 1 lines, summarized."
    )


def test_approving_one_of_two_identical_lines_leaves_the_other() -> None:
    changes = parse(_new_file("b.py", "pass", "pass"))
    plan = Plan.start("head-1", changes)
    plan.add_chunk(PlanChunk(title="First", lines=_refs(changes, 1)))
    plan.add_chunk(PlanChunk(title="Second", lines=_refs(changes, 2)))
    walk = Walk(head_sha="head-1")
    walk.on_screen = Reader.of(walk, plan, Counter()).show(plan.chunks[1])
    walk.on_screen.message_ts = "1.0"
    walk.approve("1.0")

    reader = Reader.of(walk, plan, Counter({_key(plan.chunks[1].lines[0]): 1}))

    assert reader.next_chunk() is plan.chunks[0]
    assert reader.remaining(plan.chunks[1].lines) == []


def test_a_push_that_changes_a_binary_file_reopens_an_approved_other() -> None:
    plan = Plan.start("head-1", parse(_binary("logo.png", "a")))
    walk = Walk(head_sha="head-1")
    walk.on_screen = Reader.of(walk, plan, Counter()).show_other()
    walk.on_screen.message_ts = "1.0"
    walk.approve("1.0")

    def pending(diff: str) -> bool:
        changes = parse(diff)
        return Reader.of(walk, plan.carried_to("head-2", changes), Counter()).other_pending

    assert not pending(_binary("logo.png", "a"))
    assert pending(_binary("logo.png", "b"))
    assert pending(_binary("logo.png", "a") + _binary("new.png", "c"))


def test_a_push_keeps_an_unchanged_chunk_on_screen_and_takes_down_a_changed_one() -> None:
    changes = parse(_new_file("a.py", "import os", "def one():", "def two():"))
    plan = _plan(changes)
    walk = Walk(head_sha="head-1")
    walk.on_screen = Reader.of(walk, plan, Counter()).show(plan.chunks[0])

    shifted = parse(_new_file("a.py", "import os", "", "def one():", "def two():"))
    kept, gone = walk.moved_to("head-2", shifted)
    edited = parse(_new_file("a.py", "import os", "def one(x):", "def two():"))
    dropped, taken_down = walk.moved_to("head-3", edited)

    assert gone is None
    assert kept.on_screen is not None
    assert [r.lineno for r in kept.on_screen.lines] == [3]
    assert dropped.on_screen is None
    assert taken_down is not None and taken_down.title == "One"
