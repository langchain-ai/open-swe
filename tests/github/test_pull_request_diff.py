"""``build_pr_diff_files`` against a real git repository standing in for GitHub.

The fake API answers from ``git`` itself: the PR's file list is the merge-base
diff, ``compare`` reports ``git merge-base``, and ``contents`` is ``git show``.
The base branch keeps moving after the PR forks, which is when reading
"before" at the base tip goes wrong.
"""

import shutil
import subprocess
from pathlib import Path

import httpx2
import pytest

from agent.github.pull_request_diff import build_pr_diff_files

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _numstat(repo: Path, old: str, new: str) -> tuple[int, int]:
    """Added/deleted line counts between two contents, as git counts them."""
    (repo.parent / "old").write_text(old)
    (repo.parent / "new").write_text(new)
    result = subprocess.run(
        ["git", "diff", "--no-index", "--numstat", "old", "new"],
        cwd=repo.parent,
        capture_output=True,
        text=True,
    )
    added, deleted, _ = result.stdout.split("\t", 2)
    return int(added), int(deleted)


class _GitBackedGitHub:
    """Serves the GitHub endpoints ``build_pr_diff_files`` reads, straight from git."""

    def __init__(self, repo: Path, base: str, head: str) -> None:
        self.repo, self.base, self.head = repo, base, head

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        prefix = "/repos/acme/app"
        if path == f"{prefix}/pulls/7":
            return httpx2.Response(
                200, json={"base": {"sha": self.base}, "head": {"sha": self.head}}
            )
        if path == f"{prefix}/pulls/7/files":
            merge_base = _git(self.repo, "merge-base", self.base, self.head).strip()
            files = []
            for row in _git(
                self.repo, "diff", "--numstat", "--no-renames", merge_base, self.head
            ).splitlines():
                added, deleted, name = row.split("\t")
                files.append(
                    {
                        "filename": name,
                        "status": "modified",
                        "additions": int(added),
                        "deletions": int(deleted),
                    }
                )
            return httpx2.Response(200, json=files)
        if path == f"{prefix}/compare/{self.base}...{self.head}":
            merge_base = _git(self.repo, "merge-base", self.base, self.head).strip()
            return httpx2.Response(200, json={"merge_base_commit": {"sha": merge_base}})
        if path.startswith(f"{prefix}/contents/"):
            name = path.removeprefix(f"{prefix}/contents/")
            ref = request.url.params["ref"]
            return httpx2.Response(200, content=_git(self.repo, "show", f"{ref}:{name}").encode())
        return httpx2.Response(404)


@pytest.fixture
def forked_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A PR branch forked from main, with main moving on in the same file afterwards."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    config = repo / "config.py"
    config.write_text("".join(f"line_{n} = {n}\n" for n in range(1, 41)))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    merge_base = _git(repo, "rev-parse", "HEAD").strip()

    _git(repo, "checkout", "-qb", "feature")
    lines = config.read_text().splitlines(keepends=True)
    del lines[29]
    lines.insert(29, "line_30 = 'changed by the PR'\n")
    config.write_text("".join(lines))
    _git(repo, "commit", "-qam", "feature")
    head = _git(repo, "rev-parse", "HEAD").strip()

    _git(repo, "checkout", "-q", "main")
    config.write_text("# added on main\n# after the fork\n" + config.read_text())
    _git(repo, "commit", "-qam", "main moves on")
    base_tip = _git(repo, "rev-parse", "HEAD").strip()
    return repo, merge_base, base_tip, head


async def test_before_is_the_merge_base_so_the_diff_matches_git(
    forked_repo: tuple[Path, str, str, str],
) -> None:
    repo, merge_base, base_tip, head = forked_repo
    github = _GitBackedGitHub(repo, base_tip, head)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client:
        diff = await build_pr_diff_files(client, "acme/app", 7)

    [file] = diff["files"]
    assert diff["base_sha"] == merge_base
    assert file["originalContent"] == _git(repo, "show", f"{merge_base}:config.py")
    assert _numstat(repo, file["originalContent"], file["modifiedContent"]) == (1, 1)
    # The base tip would have shown main's two new lines as deletions by the PR.
    assert _numstat(repo, _git(repo, "show", f"{base_tip}:config.py"), file["modifiedContent"]) == (
        1,
        3,
    )
