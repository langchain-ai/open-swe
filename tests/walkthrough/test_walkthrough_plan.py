import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from openswe.review_guide.walk import Reader, Walk
from openswe.walkthrough.diff import FileChange, parse
from openswe.walkthrough.plan import FileRanges, LineRef, Plan, PlanChunk, RangeError, claim
from openswe.walkthrough.render import render_chunk

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

BASE = "import os\n\ndef a():\n    return 1\n"
MODULE = (
    '"""Sessions."""\n'
    "\n"
    "from uuid import UUID\n"
    "\n"
    "from sqlalchemy import select\n"
    "\n"
    "\n"
    "class Session:\n"
    "    thread_id: str\n"
    "    user_id: UUID\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for name, content in files.items():
        if content is None:
            _git(repo, "rm", "-q", name)
        else:
            (repo / name).write_text(content)
            _git(repo, "add", name)
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _changes(repo: Path, base: str, head: str) -> list[FileChange]:
    return parse(_git(repo, "diff", "-U0", "--no-renames", f"{base}...{head}"))


def _head(repo: Path, head: str, changes: list[FileChange]) -> dict[str, list[str]]:
    return {
        c.path: _git(repo, "show", f"{head}:{c.path}").splitlines()
        for c in changes
        if not c.deleted
    }


def _added(changes: list[FileChange]) -> dict[str, set[int]]:
    return {c.path: {line.lineno for line in c.lines if line.sign == "+"} for c in changes}


def _place(plan: Plan, changes: list[FileChange], title: str, *ranges: FileRanges) -> PlanChunk:
    chunk = PlanChunk(title=title, lines=claim(list(ranges), plan.unplanned(changes)))
    plan.add_chunk(chunk)
    return chunk


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    _commit(repo, {"core.py": BASE, "gone.txt": "x\n"}, "base")
    return repo


def test_a_new_file_shows_its_class_as_source_and_leaves_its_imports_to_other(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"sessions.py": MODULE}, "add sessions")
    changes = _changes(repo, base, head)
    plan = Plan.start(head, changes)

    plan.add_other(claim([FileRanges(path="sessions.py", added=[(1, 7)])], plan.unplanned(changes)))
    chunk = _place(plan, changes, "Session", FileRanges(path="sessions.py", added=[(1, 10)]))
    index = {LineRef.of(line): line for c in changes for line in c.lines}
    rendered = render_chunk(
        [index[ref] for ref in chunk.lines], _head(repo, head, changes), _added(changes)
    )

    assert [ref.lineno for ref in chunk.lines] == [8, 9, 10]
    assert plan.unplanned(changes) == []
    assert rendered == (
        "`sessions.py` L8–10\n```python\nclass Session:\n    thread_id: str\n    user_id: UUID\n```"
    )


def test_a_changed_line_renders_as_a_diff_with_unchanged_context(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"core.py": "import os\n\ndef a():\n    return 2\n"}, "return 2")
    changes = _changes(repo, base, head)

    rendered = render_chunk(changes[0].lines, _head(repo, head, changes), _added(changes))

    assert rendered == (
        "`core.py`\n```diff\n@@ -2,3 +2,3 @@\n \n def a():\n-    return 1\n+    return 2\n```"
    )


def test_a_line_cannot_be_placed_twice(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"core.py": "import os\n\ndef a():\n    return 2\n"}, "return 2")
    changes = _changes(repo, base, head)
    plan = Plan.start(head, changes)
    return_2 = FileRanges(path="core.py", added=[(4, 4)])

    _place(plan, changes, "Return 2", return_2)

    with pytest.raises(RangeError, match="holds no available line"):
        _place(plan, changes, "Again", return_2)


def test_a_push_keeps_planned_lines_by_content_and_leaves_only_edits_unplanned(
    repo: Path,
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(
        repo,
        {"cli.py": "import sys\nprint(1)\n", "core.py": "import os\n\ndef a():\n    return 10\n"},
        "f",
    )
    changes = _changes(repo, base, head)
    plan = Plan.start(head, changes)
    plan.add_other(claim([FileRanges(path="cli.py", added=[(1, 1)])], plan.unplanned(changes)))
    _place(plan, changes, "Print one", FileRanges(path="cli.py", added=[(2, 2)]))
    _place(plan, changes, "Return 10", FileRanges(path="core.py", added=[(4, 4)], deleted=[(4, 4)]))

    new_head = _commit(
        repo,
        {"cli.py": "import sys\n\nprint(1)\n", "core.py": "import os\n\ndef a():\n    return 11\n"},
        "moved",
    )
    new_changes = _changes(repo, base, new_head)
    moved = plan.carried_to(new_head, new_changes)

    assert [(c.title, [(r.sign, r.lineno) for r in c.lines]) for c in moved.chunks] == [
        ("Print one", [("+", 3)]),
        ("Return 10", [("-", 4)]),
    ]
    assert [(r.path, r.lineno) for r in moved.other] == [("cli.py", 1)]
    assert [(line.path, line.text) for line in moved.unplanned(new_changes)] == [
        ("cli.py", ""),
        ("core.py", "    return 11"),
    ]


def test_approved_lines_stay_approved_after_a_rebase(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(
        repo, {"core.py": "import os\n\ndef a():\n    return 10\n", "cli.py": "print(1)\n"}, "f"
    )
    changes = _changes(repo, base, head)
    seen = Counter(line.key for c in changes for line in c.lines if line.path == "core.py")

    _git(repo, "checkout", "-q", "main")
    new_base = _commit(repo, {"other.txt": "unrelated\n"}, "main moves on")
    _git(repo, "checkout", "-qb", "rebased")
    new_head = _commit(
        repo,
        {"core.py": "import os\n\ndef a():\n    return 10\n", "cli.py": "print(1)\nprint(2)\n"},
        "f, rebased",
    )
    new_changes = _changes(repo, new_base, new_head)
    plan = Plan.start(new_head, new_changes)
    _place(
        plan,
        new_changes,
        "Everything",
        FileRanges(path="core.py", added=[(1, 9)], deleted=[(1, 9)]),
    )
    _place(plan, new_changes, "CLI", FileRanges(path="cli.py", added=[(1, 2)]))
    reader = Reader.of(Walk(head_sha=new_head), plan, seen)

    assert [c.title for c in plan.chunks if reader.remaining(c.lines)] == ["CLI"]
    assert reader.next_chunk() is plan.chunks[1]
