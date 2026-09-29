import asyncio
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.review_guide import git
from agent.review_guide.hunks import changed_line_keys, line_key
from agent.review_guide.render import MessageRenderer, RenderError

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

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            Path(path).write_bytes(content)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@dataclass
class _Repos:
    """Commits are made in ``origin``; the guide works in ``checkout``, which fetches them."""

    origin: Path
    checkout: Path
    patch: Path

    def commit(self, branch: str, files: dict[str, str | None], *, start: str = "") -> str:
        _git(self.origin, "checkout", "-q", *(["-b", branch, start] if start else [branch]))
        for name, content in files.items():
            if content is None:
                _git(self.origin, "rm", "-q", name)
            else:
                (self.origin / name).write_text(content)
                _git(self.origin, "add", name)
        _git(self.origin, "commit", "-qm", branch)
        _git(self.checkout, "fetch", "-q", str(self.origin), "+refs/heads/*:refs/remotes/o/*")
        return _git(self.origin, "rev-parse", "HEAD")

    def stage(self, patch: str) -> None:
        self.patch.write_text(patch)
        _git(self.checkout, "apply", "--cached", "--unidiff-zero", str(self.patch))

    async def rebuild(self, base: str, head: str, seen: Counter[str]) -> git.Rebuild:
        return await git.rebuild(
            _LocalShell(),
            str(self.checkout),
            base_sha=base,
            head_sha=head,
            seen=seen,
            patch_path=str(self.patch.with_name("carried.patch")),
        )


@pytest.fixture
def repos(tmp_path: Path) -> _Repos:
    origin, checkout = tmp_path / "origin", tmp_path / "checkout"
    for repo in (origin, checkout):
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.name", "t")
        _git(repo, "config", "user.email", "t@example.com")
    (origin / "core.py").write_text("import os\n\ndef a():\n    return 1\n")
    (origin / "gone.txt").write_text("x\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "base")
    _git(checkout, "fetch", "-q", str(origin), "+refs/heads/*:refs/remotes/o/*")
    return _Repos(origin=origin, checkout=checkout, patch=tmp_path / "chunk.patch")


async def test_approved_lines_are_not_shown_again_after_a_rebase(repos: _Repos) -> None:
    base = _git(repos.origin, "rev-parse", "main")
    head = repos.commit(
        "feature",
        {
            "core.py": "import os\n\ndef a():\n    return 10\n\ndef c():\n    return a()\n",
            "cli.py": "from core import c\nprint(c())\n",
            "gone.txt": None,
        },
        start="main",
    )
    shell = _LocalShell()
    checkout = str(repos.checkout)
    assert (await repos.rebuild(base, head, Counter())).carried_lines == 0

    repos.stage(
        "diff --git a/core.py b/core.py\n--- a/core.py\n+++ b/core.py\n"
        "@@ -4 +4 @@\n-    return 1\n+    return 10\n"
        "diff --git a/cli.py b/cli.py\nnew file mode 100644\n--- /dev/null\n+++ b/cli.py\n"
        "@@ -0,0 +1 @@\n+from core import c\n"
    )
    await git.mark_shown(shell, checkout)
    assert await git.shown_matches_index(shell, checkout)
    seen = changed_line_keys(await git.staged_diff(shell, checkout, zero=True))
    await git.commit_approved(shell, checkout, "Return 10 from `a`")

    # A force-push onto a newer base, keeping the approved lines and adding new ones around them.
    new_base = repos.commit("main", {"other.txt": "unrelated\n"})
    new_head = repos.commit(
        "feature-rebased",
        {
            "core.py": "import os\n\ndef a():\n    return 10\n\ndef c():\n    return a() + 1\n",
            "cli.py": "from core import c\nprint(c())\nprint('done')\n",
            "gone.txt": None,
        },
        start="main",
    )
    rebuilt = await repos.rebuild(new_base, new_head, seen)

    assert rebuilt.carried_lines == 3
    assert await git.built_for(shell, checkout) == (new_base, new_head)
    assert changed_line_keys(_git(repos.checkout, "diff", "-U0", "--no-renames") + "\n") == Counter(
        [
            line_key("core.py", "+", ""),
            line_key("core.py", "+", "def c():"),
            line_key("core.py", "+", "    return a() + 1"),
            line_key("cli.py", "+", "print(c())"),
            line_key("cli.py", "+", "print('done')"),
            line_key("gone.txt", "-", "x"),
        ]
    )
    _git(repos.checkout, "add", "-A")
    assert _git(repos.checkout, "write-tree") == _git(
        repos.checkout, "rev-parse", f"{new_head}^{{tree}}"
    )


async def test_restaging_after_showing_is_not_the_chunk_the_reader_saw(repos: _Repos) -> None:
    base = _git(repos.origin, "rev-parse", "main")
    head = repos.commit("f", {"core.py": "import sys\n\ndef a():\n    return 2\n"}, start="main")
    shell = _LocalShell()
    await repos.rebuild(base, head, Counter())
    repos.stage(
        "diff --git a/core.py b/core.py\n--- a/core.py\n+++ b/core.py\n"
        "@@ -4 +4 @@\n-    return 1\n+    return 2\n"
    )
    await git.mark_shown(shell, str(repos.checkout))
    _git(repos.checkout, "add", "core.py")

    assert not await git.shown_matches_index(shell, str(repos.checkout))


async def test_messages_quote_the_checkout_and_cannot_escape_the_sandbox(repos: _Repos) -> None:
    base = _git(repos.origin, "rev-parse", "main")
    head = repos.commit("f", {"core.py": "import os\n\ndef a():\n    return 2\n"}, start="main")
    await repos.rebuild(base, head, Counter())
    _git(repos.checkout, "add", "core.py")
    renderer = MessageRenderer(_LocalShell(), str(repos.checkout))

    text = await renderer.render('Returns 2 now.\n{{ staged() }}\n{{ code("core.py", 3, 4) }}')

    assert "+    return 2" in text
    assert "```py\ndef a():\n    return 2\n```" in text
    assert renderer.quoted_chunk
    with pytest.raises(RenderError):
        await renderer.render("{{ staged.__globals__ }}")
