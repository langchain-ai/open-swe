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
from fastapi import HTTPException

from openswe.github.http import GitHubClient
from openswe.github.pull_request_diff import (
    build_pr_diff_files,
    fetch_file_versions,
    pull_request_patches,
    resolve_merge_base,
)

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
        diff = await build_pr_diff_files(GitHubClient(client).repo("acme", "app").pull_request(7))

    [file] = diff["files"]
    assert diff["base_sha"] == merge_base
    assert file["originalContent"] == _git(repo, "show", f"{merge_base}:config.py")
    assert _numstat(repo, file["originalContent"], file["modifiedContent"]) == (1, 1)
    # The base tip would have shown main's two new lines as deletions by the PR.
    assert _numstat(repo, _git(repo, "show", f"{base_tip}:config.py"), file["modifiedContent"]) == (
        1,
        3,
    )


async def test_patches_name_the_base_tip_and_contents_read_its_merge_base(
    forked_repo: tuple[Path, str, str, str],
) -> None:
    repo, merge_base, base_tip, head = forked_repo
    github = _GitBackedGitHub(repo, base_tip, head)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client:
        repository = GitHubClient(client).repo("acme", "app")
        diff = await pull_request_patches(repository.pull_request(7))
        contents = await fetch_file_versions(
            repository,
            "config.py",
            "config.py",
            await resolve_merge_base(repository, diff["base_sha"], diff["head_sha"]),
            diff["head_sha"],
        )

    assert diff["base_sha"] == base_tip
    assert contents["originalContent"] == _git(repo, "show", f"{merge_base}:config.py")


async def test_every_page_of_a_large_pr_is_loaded_even_past_a_stale_count() -> None:
    names = [f"src/file_{index:03}.py" for index in range(250)]

    def github(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/pulls/7"):
            return httpx2.Response(
                200,
                json={"base": {"sha": "b" * 40}, "head": {"sha": "a" * 40}, "changed_files": 150},
            )
        if path.endswith("/pulls/7/files"):
            page = int(request.url.params.get("page", "1"))
            size = int(request.url.params["per_page"])
            batch = names[(page - 1) * size : page * size]
            return httpx2.Response(
                200,
                json=[
                    {"filename": name, "status": "added", "additions": 1, "deletions": 0}
                    for name in batch
                ],
            )
        if "/compare/" in path:
            return httpx2.Response(200, json={"merge_base_commit": {"sha": "b" * 40}})
        return httpx2.Response(200, content=b"x = 1\n")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client:
        diff = await build_pr_diff_files(GitHubClient(client).repo("acme", "app").pull_request(7))

    assert [file["path"] for file in diff["files"]] == names
    assert diff["truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("malicious", ["../../../private/repo/issues/1", "/private", "a/../b"])
async def test_hydration_rejects_traversal_before_network(malicious: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail(f"unexpected request: {request.url}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        for path, original in [(malicious, "safe.py"), ("safe.py", malicious)]:
            with pytest.raises(HTTPException) as exc:
                await fetch_file_versions(
                    GitHubClient(client).repo("acme", "app"), path, original, "a" * 40, "b" * 40
                )
            assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_hydration_reads_snapshot_and_keeps_unavailable_blob_null() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        ref = request.url.params["ref"]
        assert ref in ("a" * 40, "b" * 40)
        return httpx2.Response(200, text="before") if ref == "a" * 40 else httpx2.Response(500)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        contents = await fetch_file_versions(
            GitHubClient(client).repo("acme", "app"), "safe.py", "safe.py", "a" * 40, "b" * 40
        )
    assert contents == {"originalContent": "before", "modifiedContent": None}
