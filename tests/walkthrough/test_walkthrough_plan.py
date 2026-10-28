import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.review_guide.walk import Reader, Walk
from openswe.sandbox_scripts.walkthrough import parse_index
from openswe.walkthrough.checkout import HEAD_REF, MERGE_BASE_REF, CheckoutError, PinnedCheckout
from openswe.walkthrough.diff import Carry, DiffIndex
from openswe.walkthrough.lines import LinePoint, LineSet
from openswe.walkthrough.plan import (
    FileHunks,
    FileRanges,
    FileSplits,
    Plan,
    PlanChunk,
    RangeError,
    claim,
)
from openswe.walkthrough.planner import PlanWorkspace
from openswe.walkthrough.record import Walkthrough

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


class _ShellBackend:
    async def aexecute(self, command: str, timeout: int | None = None) -> ExecuteResponse:  # noqa: ARG002
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
        return ExecuteResponse(output=result.stdout + result.stderr, exit_code=result.returncode)


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


async def _pin(checkout: PinnedCheckout, base: str, head: str) -> DiffIndex:
    """Pin ``checkout`` as a planner would, and index its diff in the "sandbox"."""
    repo = Path(checkout.repo_dir)
    _git(repo, "update-ref", HEAD_REF, head)
    _git(repo, "update-ref", MERGE_BASE_REF, _git(repo, "merge-base", base, head).strip())
    return await checkout.index()


async def _carry(
    checkout: PinnedCheckout, old_head: str, base: str, head: str
) -> tuple[DiffIndex, Carry]:
    old_merge_base = _git(Path(checkout.repo_dir), "rev-parse", MERGE_BASE_REF).strip()
    index = await _pin(checkout, base, head)
    carry = await checkout.carry(old_head, old_merge_base, index)
    assert carry is not None
    return index, carry


def _place(plan: Plan, index: DiffIndex, title: str, *hunks: FileHunks) -> PlanChunk:
    lines = plan.hunks(index).claim(list(hunks), plan.unplanned(index))
    chunk = PlanChunk(title=title, lines=lines)
    plan.add_chunk(chunk)
    return chunk


def _added(path: str, *spans: tuple[int, int]) -> LineSet:
    return LineSet.of(path, "+", spans)


def _deleted(path: str, *spans: tuple[int, int]) -> LineSet:
    return LineSet.of(path, "-", spans)


def _stored_as(monkeypatch: pytest.MonkeyPatch, plan: Plan) -> None:
    """Make ``plan`` itself the stored plan that workspace edits change."""

    async def edit(_: object, *, change: Callable[[Plan], object], **__: object) -> Plan:
        change(plan)
        return plan

    monkeypatch.setattr(Walkthrough, "edit", edit)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "work" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    _commit(repo, {"core.py": BASE, "gone.txt": "x\n"}, "base")
    return repo


@pytest.fixture
def checkout(repo: Path) -> PinnedCheckout:
    return PinnedCheckout(cast(SandboxBackendProtocol, _ShellBackend()), str(repo))


async def test_a_cut_of_a_new_file_renders_its_class_as_source(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"sessions.py": MODULE}, "add sessions")
    index = await _pin(checkout, base, head)
    plan = Plan.start(head, index)

    plan.add_other(claim([FileRanges(path="sessions.py", added=[(1, 7)])], plan.unplanned(index)))
    lines = claim([FileRanges(path="sessions.py", added=[(1, 10)])], plan.unplanned(index))

    assert lines == _added("sessions.py", (8, 10))
    assert await checkout.render(lines) == (
        "`sessions.py` L8–10\n```python\nclass Session:\n    thread_id: str\n    user_id: UUID\n```"
    )


async def test_a_changed_line_renders_as_a_diff_with_unchanged_context(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"core.py": "import os\n\ndef a():\n    return 2\n"}, "return 2")
    index = await _pin(checkout, base, head)

    assert await checkout.render(index.lines) == (
        "`core.py`\n```diff\n@@ -2,3 +2,3 @@\n \n def a():\n-    return 1\n+    return 2\n```"
    )


async def test_context_separated_deletion_and_addition_render_separately(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _commit(repo, {"lines.txt": "one\ntwo\nthree\nfour\nsix\n"}, "lines")
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"lines.txt": "one\nthree\nfour\nFIVE\nsix\n"}, "edit lines")
    index = await _pin(checkout, base, head)

    assert await checkout.render(index.lines) == (
        "`lines.txt`\n```diff\n@@ -1,4 +1,3 @@\n one\n-two\n three\n four\n```\n"
        "`lines.txt` L4\n```txt\nFIVE\n```"
    )


async def test_a_chunk_too_large_for_a_command_line_still_renders(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"lines.txt": "".join(f"line {n}\n" for n in range(1, 30_001))}, "lines")
    await _pin(checkout, base, head)
    # Every other line is its own range, so the request is far past one argument's limit.
    scattered = _added("lines.txt", *((n, n) for n in range(1, 30_001, 2)))

    code = await checkout.render(scattered)

    assert code.count("`lines.txt` L") == 15_000
    assert code.startswith("`lines.txt` L1\n```txt\nline 1\n```")


async def test_a_chunk_takes_whole_hunks_across_files_and_a_hunk_is_placed_once(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(
        repo,
        {"core.py": "import os\n\ndef a():\n    return 2\n", "cli.py": "print(a())\n"},
        "return 2",
    )
    index = await _pin(checkout, base, head)
    plan = Plan.start(head, index)
    both = (FileHunks(path="core.py", hunks=["-4"]), FileHunks(path="cli.py", hunks=["+1"]))

    chunk = _place(plan, index, "Return 2 and print it", *both)

    assert chunk.lines == _added("cli.py", (1, 1)) | _added("core.py", (4, 4)) | _deleted(
        "core.py", (4, 4)
    )
    with pytest.raises(RangeError, match="no available hunk -4"):
        _place(plan, index, "Again", both[0])


async def test_a_push_keeps_planned_lines_git_kept_and_new_lines_join_their_hunks_chunk(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(
        repo,
        {"cli.py": "import sys\nprint(1)\n", "core.py": "import os\n\ndef a():\n    return 10\n"},
        "f",
    )
    index = await _pin(checkout, base, head)
    plan = Plan.start(head, index)
    plan.add_other(claim([FileRanges(path="cli.py", added=[(1, 1)])], plan.unplanned(index)))
    _place(plan, index, "Print one", FileHunks(path="cli.py", hunks=["+1"]))
    _place(plan, index, "Return 10", FileHunks(path="core.py", hunks=["-4"]))

    new_head = _commit(
        repo,
        {"cli.py": "import sys\n\nprint(1)\n", "core.py": "import os\n\ndef a():\n    return 11\n"},
        "moved",
    )
    new_index, carry = await _carry(checkout, head, base, new_head)
    moved = plan.carried_to(new_head, new_index, carry)

    assert [(c.title, c.lines) for c in moved.chunks] == [
        ("Print one", _added("cli.py", (2, 3))),
        ("Return 10", _deleted("core.py", (4, 4)) | _added("core.py", (4, 4))),
    ]
    assert moved.other == _added("cli.py", (1, 1))
    assert not moved.unplanned(new_index)


async def test_approved_lines_stay_approved_after_a_rebase(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(
        repo, {"core.py": "import os\n\ndef a():\n    return 10\n", "cli.py": "print(1)\n"}, "f"
    )
    index = await _pin(checkout, base, head)
    seen = index.lines.only(["core.py"])

    _git(repo, "checkout", "-q", "main")
    new_base = _commit(repo, {"other.txt": "unrelated\n"}, "main moves on")
    _git(repo, "checkout", "-qb", "rebased")
    new_head = _commit(
        repo,
        {"core.py": "import os\n\ndef a():\n    return 10\n", "cli.py": "print(1)\nprint(2)\n"},
        "f, rebased",
    )
    new_index, carry = await _carry(checkout, head, new_base, new_head)
    plan = Plan.start(new_head, new_index)
    _place(plan, new_index, "Everything", FileHunks(path="core.py", hunks=["-4"]))
    _place(plan, new_index, "CLI", FileHunks(path="cli.py", hunks=["+1"]))
    reader = Reader.of(Walk(head_sha=new_head), plan, carry.lines(seen))

    assert [c.title for c in plan.chunks if reader.remaining(c.lines)] == ["CLI"]
    assert reader.next_chunk() is plan.chunks[1]
    # Deleted lines are numbered at the old merge base, so without it they are dropped, not guessed.
    blind = await checkout.carry(head, "", new_index)
    assert blind is not None
    assert blind.lines(seen) == seen.only(["core.py"]) - _deleted("core.py", (4, 4))


async def test_a_split_hunk_goes_to_two_chunks_and_each_part_keeps_its_new_lines_after_a_push(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    jobs = "def one():\n    return 1\n\n\ndef two():\n    return 2\n"
    head = _commit(repo, {"jobs.py": jobs}, "jobs")
    index = await _pin(checkout, base, head)
    plan = Plan.start(head, index)
    plan.split([LinePoint(path="jobs.py", sign="+", lineno=5)])

    _place(plan, index, "One", FileHunks(path="jobs.py", hunks=["+1"]))
    _place(plan, index, "Two", FileHunks(path="jobs.py", hunks=["+5"]))

    assert [c.lines for c in plan.chunks] == [_added("jobs.py", (1, 4)), _added("jobs.py", (5, 6))]

    new_head = _commit(
        repo, {"jobs.py": f"import os\n\n\n{jobs}\n\ndef three():\n    return 3\n"}, "more"
    )
    new_index, carry = await _carry(checkout, head, base, new_head)
    moved = plan.carried_to(new_head, new_index, carry)

    assert [c.lines for c in moved.chunks] == [
        _added("jobs.py", (1, 7)),
        _added("jobs.py", (8, 13)),
    ]
    assert not moved.unplanned(new_index)


async def test_a_push_keeps_repeated_lines_with_their_own_chunk(
    repo: Path, checkout: PinnedCheckout
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    jobs = "def one():\n    run(1)\n    return\n\n\ndef two():\n    run(2)\n    return\n"
    head = _commit(repo, {"jobs.py": jobs}, "jobs")
    index = await _pin(checkout, base, head)
    plan = Plan.start(head, index)
    plan.split([LinePoint(path="jobs.py", sign="+", lineno=6)])
    _place(plan, index, "Two", FileHunks(path="jobs.py", hunks=["+6"]))
    _place(plan, index, "One", FileHunks(path="jobs.py", hunks=["+1"]))

    new_head = _commit(repo, {"jobs.py": f"import os\n\n\n{jobs}"}, "import")
    new_index, carry = await _carry(checkout, head, base, new_head)
    moved = plan.carried_to(new_head, new_index, carry)

    assert [(c.title, c.lines) for c in moved.chunks] == [
        ("Two", _added("jobs.py", (9, 11))),
        ("One", _added("jobs.py", (1, 8))),
    ]


async def test_a_split_that_leaves_only_closing_braces_is_a_fragment(
    repo: Path, checkout: PinnedCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    head = _commit(repo, {"link.go": "func a() {\n\tif ok {\n\t\treturn\n\t}\n}\n"}, "go")
    index = await _pin(checkout, base, head)
    plan = Plan.start(head, index)
    _stored_as(monkeypatch, plan)
    workspace = PlanWorkspace(AsyncMock(), checkout, head, base, index, plan)

    with pytest.raises(RangeError, match="`link.go` \\+4"):
        await workspace.split([FileSplits(path="link.go", at=["+4"])])
    await workspace.split([FileSplits(path="link.go", at=["+2"])])

    assert plan.splits == [LinePoint(path="link.go", sign="+", lineno=2)]


async def test_replacing_chunks_merges_steps_and_releases_omitted_hunks(
    repo: Path, checkout: PinnedCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    head = _commit(repo, {"a.py": "a = 1\n", "b.py": "b = 2\n", "c.py": "c = 3\n"}, "add")
    index = await _pin(checkout, base, head)
    plan = Plan.start(head, index)
    hunks = [FileHunks(path=name, hunks=["+1"]) for name in ["a.py", "b.py", "c.py"]]
    _place(plan, index, "First", hunks[0], hunks[2])
    _place(plan, index, "Second", hunks[1])
    rendering = AsyncMock()
    rendering.render.return_value = "merged code"
    workspace = PlanWorkspace(AsyncMock(), rendering, head, base, index, plan)

    async def edit(change: Callable[[Plan], object]) -> None:
        change(plan)

    monkeypatch.setattr(workspace, "_edit", edit)
    assert (
        await workspace.plan_chunk(
            title="Merged", show=hunks[:2], explanation="Combined", replace=[1, 2]
        )
        == 1
    )
    assert [chunk.title for chunk in plan.chunks] == ["Merged"]
    assert set(plan.chunks[0].lines.paths) == {"a.py", "b.py"}
    assert plan.unplanned(index).paths == ["c.py"]
    assert not plan.other

    async def concurrent_edit(change: Callable[[Plan], object]) -> None:
        plan.chunks[0].explanation = "Another planner revised this"
        change(plan)

    monkeypatch.setattr(workspace, "_edit", concurrent_edit)
    with pytest.raises(RangeError, match="another planner changed"):
        await workspace.plan_chunk(title="Stale", show=hunks[:2], explanation="Old", replace=[1])
    assert plan.chunks[0].title == "Merged"


async def test_planning_a_huge_pull_request_costs_its_hunks_not_its_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Planning runs on the event loop, so per-line work would freeze the server here.
    files = 2_000
    diff = "".join(
        f"diff --git a/f{i}.json b/f{i}.json\n--- a/f{i}.json\n+++ b/f{i}.json\n"
        "@@ -0,0 +1,360 @@\n" + "+x\n" * 360
        for i in range(files)
    ).splitlines()
    index = DiffIndex.model_validate({"files": parse_index(diff)})
    plan = Plan.start("head", index)
    plan.add_other(index.lines.only(f"f{i}.json" for i in range(files // 2)))
    _stored_as(monkeypatch, plan)
    workspace = PlanWorkspace(AsyncMock(), AsyncMock(), "head", "base", index, plan)
    workspace.checkout.wordless.side_effect = lambda sets: [False] * len(sets)

    started = time.perf_counter()
    await workspace.split(
        [FileSplits(path=f"f{i}.json", at=["+100"]) for i in range(files // 2, files)]
    )
    await workspace.move_to_other(
        [FileHunks(path=f"f{i}.json", hunks=["+1", "+100"]) for i in range(files // 2, files)]
    )
    status = workspace.status()

    assert time.perf_counter() - started < 5
    assert (status.other_lines, status.unplanned) == (files * 360, "nothing")


@pytest.mark.usefixtures("registry_db")
async def test_a_plan_stored_line_by_line_reads_as_ranges_and_gets_its_index() -> None:
    pull_request = await PullRequest(owner="o", repo="r", number=1).ensure()

    def line(lineno: int) -> dict[str, object]:
        return {"path": "a.py", "sign": "+", "lineno": lineno, "text": f"line {lineno}"}

    async with postgres.session() as session:
        session.add(
            Walkthrough(
                pull_request_id=pull_request.id,
                head_sha="head",
                merge_base_sha="base",
                plan_json={
                    "head_sha": "head",
                    "chunks": [{"id": "c", "title": "One", "lines": [line(1), line(2), line(4)]}],
                    "other": [line(3)],
                    "splits": [line(4)],
                },
                complete=True,
            )
        )
        await session.commit()
    stored = await Walkthrough.get(pull_request.id, with_index=True)
    assert stored is not None and stored.diff_index is None
    assert stored.plan.chunks[0].lines == _added("a.py", (1, 2), (4, 4))
    assert stored.plan.other == _added("a.py", (3, 3))
    assert stored.plan.splits == [LinePoint(path="a.py", sign="+", lineno=4)]

    diff = "diff --git a/a.py b/a.py\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1,4 @@\n" + "+x\n" * 4
    index = DiffIndex.model_validate({"files": parse_index(diff.splitlines())})
    await Walkthrough.save_index(pull_request.id, "head", index)
    stored = await Walkthrough.get(pull_request.id, with_index=True)

    assert stored is not None and stored.diff_index == index
    assert stored.plan.complete(index)


@pytest.mark.usefixtures("registry_db")
async def test_a_plan_left_at_an_older_merge_base_is_replanned_without_base_branch_files(
    repo: Path, checkout: PinnedCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-qb", "feature")
    _commit(repo, {"feature.py": "x = 1\n"}, "feature")
    _git(repo, "checkout", "-q", "main")
    base = _commit(repo, {"unrelated.py": "y = 2\n"}, "landed on main")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--no-edit", "main")
    head = _git(repo, "rev-parse", "HEAD").strip()
    stale = await _pin(checkout, stale_base, head)
    assert [file.path for file in stale.files] == ["feature.py", "unrelated.py"]
    pull_request = await PullRequest(owner="o", repo="r", number=1).ensure()
    assert await Walkthrough.install(
        pull_request.id,
        Plan.start(head, stale),
        merge_base_sha=stale_base,
        index=stale,
        replacing=None,
    )
    await _pin(checkout, base, head)

    async def current_head(_: PullRequest) -> str:
        return head

    monkeypatch.setattr("openswe.walkthrough.planner._current_head", current_head)
    workspace = await PlanWorkspace.open(pull_request, checkout)

    assert workspace.merge_base_sha == base
    assert [file.path for file in workspace.index.files] == ["feature.py"]
    stored = await Walkthrough.get(pull_request.id, with_index=True)
    assert stored is not None and stored.merge_base_sha == base


async def test_a_missing_checkout_is_reported_unless_allowed(tmp_path: Path) -> None:
    missing = PinnedCheckout(cast(SandboxBackendProtocol, _ShellBackend()), str(tmp_path / "repo"))
    with pytest.raises(CheckoutError, match="Repository not found"):
        await missing.pinned()
    assert await missing.pinned(allow_missing=True) is None


async def test_a_head_change_during_planning_ends_the_scout(
    repo: Path, checkout: PinnedCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from runtime_api.loop import RunCall
    from runtime_api.operations import RunOperation

    from openswe.agent_api.graph import RunSession
    from openswe.database import postgres
    from openswe.review_scout.graph import PrepareReviewScoutRunMiddleware
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, ToolSurface
    from openswe.tools import plan_walkthrough

    base = _git(repo, "rev-parse", "HEAD").strip()
    head = _commit(repo, {"cli.py": "print(1)\n"}, "feature")
    index = await _pin(checkout, base, head)
    workspace = PlanWorkspace(AsyncMock(), checkout, head, base, index, Plan.start(head, index))
    stored = SimpleNamespace(head_sha=head)
    session = AsyncMock()
    session.scalar.return_value = stored

    @asynccontextmanager
    async def database_session() -> AsyncIterator[AsyncMock]:
        yield session

    monkeypatch.setattr(postgres, "session", database_session)
    monkeypatch.setattr(plan_walkthrough, "_workspace", AsyncMock(return_value=workspace))
    config = {"configurable": {"thread_id": "scout"}}
    model = ModelChoice("anthropic:claude-sonnet-4-5", {})
    surface = ToolSurface(
        base_tools=[plan_walkthrough.walkthrough_describe_other],
        middleware=[PrepareReviewScoutRunMiddleware(thread_id="scout", config=config)],
        models=SurfaceModels(main=model, subagent=model, use_gateway=False),
    )
    await surface.prepare()
    run = RunSession(surface, config, None, started_here=True)
    stored.head_sha = _commit(repo, {"cli.py": "print(2)\n"}, "push")

    result = await run.operation(
        RunOperation(
            name="walkthrough_describe_other",
            arguments={"summary": "Noise"},
            state={"scout_nothing_to_plan": False},
            request_id="call-1",
        )
    )
    assert result.state == {"scout_nothing_to_plan": True}
    next_step = await run.before_model(RunCall(state=result.state))
    assert next_step.jump_to == "end"
    assert next_step.call is None
    assert workspace.plan.other_summary == ""
    session.commit.assert_not_awaited()
