import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.review.walkthrough import FileLines, StepDraft
from agent.review_scout.git import (
    OTHER_TITLE,
    commit_staged,
    committed_kinds,
    finalize,
    setup_working_tree,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


@dataclass
class _Result:
    output: str
    exit_code: int


class _LocalShell:
    async def aexecute(self, command: str, timeout: int | None = None) -> _Result:  # noqa: ARG002
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        return _Result(stdout.decode(), process.returncode or 0)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def pr_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "core.py").write_text("import os\n\ndef a():\n    return 1\n\ndef b():\n    return 2\n")
    (repo / "gone.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "feature")
    (repo / "core.py").write_text(
        "import os\nimport sys\n\ndef a():\n    return 10\n\n"
        "def b():\n    return 2\n\ndef c():\n    return a() + b()\n"
    )
    (repo / "cli.py").write_text("from core import c\nprint(c())\n")
    _git(repo, "rm", "-q", "gone.txt")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "feature")
    return repo, base, _git(repo, "rev-parse", "HEAD")


async def test_steps_own_their_lines_and_the_rest_lands_in_other(
    pr_repo: tuple[Path, str, str], tmp_path: Path
) -> None:
    repo, base, head = pr_repo
    shell = _LocalShell()
    merge_base = await setup_working_tree(shell, str(repo), base_sha=base, head_sha=head)

    hunks = _git(repo, "diff", "-U0", "--", "core.py").split("\n@@")
    patch = tmp_path / "c.patch"
    patch.write_text(hunks[0] + "".join(f"\n@@{h}" for h in hunks[1:] if "def c" in h) + "\n")
    _git(repo, "apply", "--cached", "--unidiff-zero", "--recount", str(patch))
    assert await commit_staged(shell, str(repo), title="Add `c`", summary="Sums both.", other=False)
    _git(repo, "add", "--", "cli.py")
    assert await commit_staged(shell, str(repo), title="Call `c`", summary="", other=False)
    assert await commit_staged(shell, str(repo), title="Empty", summary="", other=False) is None

    steps = await finalize(shell, str(repo), merge_base=merge_base, head_sha=head)

    assert steps == [
        StepDraft(
            title="Add `c`",
            summary="Sums both.",
            files=[FileLines(path="core.py", added=[(9, 11)])],
        ),
        StepDraft(title="Call `c`", files=[FileLines(path="cli.py", added=[(1, 2)])]),
        StepDraft(
            title=OTHER_TITLE,
            is_other=True,
            files=[
                FileLines(path="core.py", added=[(2, 2), (5, 5)], deleted=[(4, 4)]),
                FileLines(path="gone.txt", deleted=[(1, 1)]),
            ],
        ),
    ]
    assert _git(repo, "rev-parse", "HEAD^{tree}") == _git(repo, "rev-parse", f"{head}^{{tree}}")


async def test_other_committed_first_is_shown_last(pr_repo: tuple[Path, str, str]) -> None:
    repo, base, head = pr_repo
    shell = _LocalShell()
    merge_base = await setup_working_tree(shell, str(repo), base_sha=base, head_sha=head)
    assert await committed_kinds(shell, str(repo), base_sha=base, head_sha=head) == []

    _git(repo, "rm", "-q", "--cached", "--", "gone.txt")
    assert await commit_staged(shell, str(repo), title="Other", summary="Drops.", other=True)
    _git(repo, "add", "--", "cli.py", "core.py")
    assert await commit_staged(shell, str(repo), title="Add `c`", summary="", other=False)
    assert await committed_kinds(shell, str(repo), base_sha=base, head_sha=head) == [True, False]

    steps = await finalize(shell, str(repo), merge_base=merge_base, head_sha=head)

    assert [(step.title, step.is_other) for step in steps] == [
        ("Add `c`", False),
        (OTHER_TITLE, True),
    ]
    assert steps[-1].files == [FileLines(path="gone.txt", deleted=[(1, 1)])]


async def test_an_empty_other_commit_adds_no_step(pr_repo: tuple[Path, str, str]) -> None:
    repo, base, head = pr_repo
    shell = _LocalShell()
    merge_base = await setup_working_tree(shell, str(repo), base_sha=base, head_sha=head)

    assert await commit_staged(shell, str(repo), title="Other", summary="", other=True)
    _git(repo, "add", "-A")
    assert await commit_staged(shell, str(repo), title="Everything", summary="", other=False)

    steps = await finalize(shell, str(repo), merge_base=merge_base, head_sha=head)

    assert [step.title for step in steps] == ["Everything"]


async def test_owned_lines_are_exactly_the_pr_diffs_changed_lines(tmp_path: Path) -> None:
    repo = tmp_path / "repeat"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    target = repo / "f"
    target.write_text("a\nb\nc\na\nb\nc\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    target.write_text("a\nb\nc\nb\nc\na\nb\nc\n")
    _git(repo, "commit", "-qam", "head")
    head = _git(repo, "rev-parse", "HEAD")
    shell = _LocalShell()
    merge_base = await setup_working_tree(shell, str(repo), base_sha=base, head_sha=head)

    # An intermediate step blame aligns differently from the PR diff, which adds head lines 4-5.
    target.write_text("a\nc\nb\nc\na\nb\nc\n")
    _git(repo, "add", "f")
    target.write_text("a\nb\nc\nb\nc\na\nb\nc\n")
    assert await commit_staged(shell, str(repo), title="Step", summary="", other=False)

    steps = await finalize(shell, str(repo), merge_base=merge_base, head_sha=head)

    owned = sorted(
        n
        for step in steps
        for file in step.files
        for start, end in file.added
        for n in range(start, end + 1)
    )
    assert owned == [4, 5]
    assert all(not file.deleted for step in steps for file in step.files)


async def test_paths_with_spaces_keep_their_lines(tmp_path: Path) -> None:
    repo = tmp_path / "spaces"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    target = repo / "foo bar.txt"
    target.write_text("a\nb\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    target.write_text("a\nB\n")
    _git(repo, "commit", "-qam", "head")
    head = _git(repo, "rev-parse", "HEAD")
    shell = _LocalShell()
    merge_base = await setup_working_tree(shell, str(repo), base_sha=base, head_sha=head)
    _git(repo, "add", "--", "foo bar.txt")
    assert await commit_staged(shell, str(repo), title="Edit", summary="", other=False)

    steps = await finalize(shell, str(repo), merge_base=merge_base, head_sha=head)

    assert steps == [
        StepDraft(
            title="Edit", files=[FileLines(path="foo bar.txt", added=[(2, 2)], deleted=[(2, 2)])]
        )
    ]
