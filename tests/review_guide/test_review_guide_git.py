import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from agent.review_guide.diff import FileChange, parse, unseen
from agent.review_guide.plan import ChunkSpec, FileRanges, PlanError, build_plan
from agent.review_guide.render import MessageRenderer, RenderError, render_chunk

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

BASE_MIGRATION = "import os\n\ndef a():\n    return 1\n"
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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    _commit(repo, {"core.py": BASE_MIGRATION, "gone.txt": "x\n"}, "base")
    return repo


def test_a_new_file_shows_its_class_as_source_and_leaves_its_imports_to_other(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"sessions.py": MODULE}, "add sessions")
    changes = _changes(repo, base, head)

    plan = build_plan(
        head,
        changes,
        unseen(changes, Counter()),
        [
            ChunkSpec(
                title="The `Session` model", files=[FileRanges(path="sessions.py", added=[(8, 10)])]
            )
        ],
    )
    rendered = render_chunk(
        [line for c in changes for line in c.lines if line.lineno >= 8],
        _head(repo, head, changes),
        _added(changes),
    )

    assert {(ref.path, ref.lineno) for ref in plan.other} == {
        ("sessions.py", n) for n in range(1, 8)
    }
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


def test_the_walkthrough_cannot_finish_until_every_chunk_and_other_is_settled(
    repo: Path,
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(
        repo,
        {"core.py": "import os\nimport sys\n\ndef a():\n    return 2\n", "gone.txt": None},
        "change",
    )
    changes = _changes(repo, base, head)
    chunk = ChunkSpec(title="Return 2", files=[FileRanges(path="core.py", added=[(5, 5)])])

    with pytest.raises(PlanError, match="already in chunk 1"):
        build_plan(head, changes, unseen(changes, Counter()), [chunk, chunk])
    with pytest.raises(PlanError, match="holds no unreviewed changed line"):
        build_plan(
            head,
            changes,
            unseen(changes, Counter()),
            [ChunkSpec(title="Nothing", files=[FileRanges(path="core.py", added=[(1, 1)])])],
        )

    plan = build_plan(head, changes, unseen(changes, Counter()), [chunk])
    assert len(plan.unfinished()) == 2
    plan.chunks[0].status = "approved"
    assert plan.unfinished() == [f"Other ({len(plan.other)} lines) is planned"]
    plan.other_status = "approved"
    assert plan.unfinished() == []
    assert (
        plan.coverage()
        == "Walked through 1 of 1 chunks (1 changed lines); Other, 3 lines, summarized."
    )


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
    left = unseen(_changes(repo, new_base, new_head), seen)

    assert [(line.path, line.sign, line.text) for line in left] == [
        ("cli.py", "+", "print(1)"),
        ("cli.py", "+", "print(2)"),
    ]


async def test_messages_quote_through_helpers_and_cannot_escape_the_sandbox() -> None:
    async def read_head(path: str) -> str:
        return "one\ntwo\nthree\n" if path == "a.py" else ""

    async def file_diff(path: str) -> str:
        return f"+{path}"

    async def chunk() -> str:
        return "CHUNK"

    renderer = MessageRenderer(read_head=read_head, file_diff=file_diff, chunk=chunk)

    text = await renderer.render('See {{ code("a.py", 2, 3) }} and {{ chunk() }}')

    assert text == "See ```python\ntwo\nthree\n``` and CHUNK"
    with pytest.raises(RenderError):
        await renderer.render("{{ chunk.__globals__ }}")
