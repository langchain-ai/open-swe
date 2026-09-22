import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.review.walkthrough import FileLines, StepDraft
from agent.review_scout.git import OTHER_TITLE, commit_staged, finalize, setup_working_tree

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
            summary=steps[-1].summary,
            is_other=True,
            files=[
                FileLines(path="core.py", added=[(2, 2), (5, 5)], deleted=[(4, 4)]),
                FileLines(path="gone.txt", deleted=[(1, 1)]),
            ],
        ),
    ]
    assert _git(repo, "rev-parse", "HEAD^{tree}") == _git(repo, "rev-parse", f"{head}^{{tree}}")
