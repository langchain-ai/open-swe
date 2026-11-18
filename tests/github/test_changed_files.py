"""A pull request's file list is read from GitHub once per base and head commit."""

import asyncio
import json
import threading

import httpx2
import pytest

from openswe.github.changed_files import ChangedFilesCache, FileList, PullRequestTips
from openswe.github.http import GitHubClient


class _PullRequest:
    """GitHub serving pull request 7 at ``head``, with ``count`` changed files."""

    def __init__(self, head: str, count: int) -> None:
        self.head, self.count = head, count
        self.base = "b" * 40
        # Applied right after the file list is served, as a retarget racing the read.
        self.retarget_to: str | None = None
        self.file_pages: list[int] = []
        self.fail = False

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/graphql":
            assert json.loads(request.content)["variables"]["number"] == 7
            tips = {"baseRefOid": self.base, "headRefOid": self.head}
            return httpx2.Response(200, json={"data": {"repository": {"pullRequest": tips}}})
        assert request.url.path == "/repos/acme/app/pulls/7/files"
        page = int(request.url.params["page"])
        self.file_pages.append(page)
        await asyncio.sleep(0)
        if self.fail:
            raise ValueError("Invalid GitHub response")
        if self.retarget_to is not None:
            self.base, self.retarget_to = self.retarget_to, None
        names = [f"src/file_{index:04}.py" for index in range(self.count)]
        batch = names[(page - 1) * 100 : page * 100]
        return httpx2.Response(
            200,
            json=[
                {
                    "filename": name,
                    "status": "modified",
                    "patch": "@@ -1 +1 @@\n-a\n+b",
                    "contents_url": f"https://api.github.com/repos/acme/app/contents/{name}"
                    f"?ref={self.head}",
                }
                for name in batch
            ],
            headers={"Link": '<https://api.github.com/next>; rel="next"'}
            if page * 100 < self.count
            else {},
        )


async def test_a_head_already_read_is_served_without_reading_its_files_again() -> None:
    github = _PullRequest("a" * 40, 3)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client:
        pull = GitHubClient(client).repo("acme", "app").pull_request(7)
        first, again = await asyncio.gather(pull.files(), pull.files())
        assert github.file_pages == [1]
        assert again == first
        first[0]["filename"] = "mutated.py"
        assert again[0]["filename"] == "src/file_0000.py"
        again[0]["filename"] = "also-mutated.py"
        cached = await pull.files()
        assert cached[0]["filename"] == "src/file_0000.py"
        assert github.file_pages == [1]

        github.head = "c" * 40
        moved = await pull.files()

    assert github.file_pages == [1, 1]
    assert moved[0]["contents_url"].endswith("c" * 40)


async def test_a_retargeted_pull_request_reads_its_files_again() -> None:
    github = _PullRequest("a" * 40, 3)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client:
        pull = GitHubClient(client).repo("acme", "app").pull_request(7)
        github.retarget_to = "d" * 40
        await pull.files()
        await pull.files()
        await pull.files()
        github.base = "e" * 40
        await pull.files()

    # The list read while the base moved is not kept; the next one is, until the next retarget.
    assert github.file_pages == [1, 1, 1]


async def test_a_truncated_list_does_not_answer_a_read_of_more_pages() -> None:
    github = _PullRequest("a" * 40, 150)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client:
        pull = GitHubClient(client).repo("acme", "app").pull_request(7)
        assert len(await pull.files()) == 100
        assert len(await pull.files(max_pages=30)) == 150
        assert len(await pull.files()) == 100
        assert len(await pull.files(max_pages=30)) == 150

    assert github.file_pages == [1, 1, 2]


@pytest.mark.parametrize("waiter_fails", [False, True])
async def test_failed_shared_reads_do_not_prevent_a_retry(waiter_fails: bool) -> None:
    github = _PullRequest("a" * 40, 3)
    github.fail = True
    other_github = _PullRequest("a" * 40, 3)
    other_github.fail = waiter_fails
    async with (
        httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client,
        httpx2.AsyncClient(transport=httpx2.MockTransport(other_github)) as other_client,
    ):
        pull = GitHubClient(client).repo("acme", "app").pull_request(7)
        other_pull = GitHubClient(other_client).repo("acme", "app").pull_request(7)
        owner, waiter = await asyncio.gather(
            pull.files(), other_pull.files(), return_exceptions=True
        )
        assert isinstance(owner, ValueError)
        if waiter_fails:
            assert isinstance(waiter, ValueError)
        else:
            assert isinstance(waiter, list) and len(waiter) == 3
        assert github.file_pages == other_github.file_pages == [1]
        github.fail = False
        assert len(await pull.files()) == 3
    assert github.file_pages == [1, 1]


class _BlockedFiles:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    async def load(self) -> FileList:
        self.calls += 1
        self.started.set()
        assert await asyncio.to_thread(self.release.wait, 5)
        return [{"status": "modified", "contents_url": "https://api.github.com/file?ref=head"}]


async def test_cancelling_a_waiter_does_not_cancel_another_loops_read() -> None:
    cache = ChangedFilesCache()
    tips = PullRequestTips("base", "head")

    async def confirm() -> PullRequestTips:
        return tips

    loader = _BlockedFiles()
    owner = asyncio.create_task(
        asyncio.to_thread(asyncio.run, cache.read("acme/app", 7, tips, 1, loader.load, confirm))
    )
    try:
        assert await asyncio.to_thread(loader.started.wait, 5)
        waiter = asyncio.create_task(cache.read("acme/app", 7, tips, 1, loader.load, confirm))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert loader.calls == 1
    finally:
        loader.release.set()
    assert len(await owner) == 1
    assert len(await cache.read("acme/app", 7, tips, 1, loader.load, confirm)) == 1
    assert loader.calls == 1


async def test_cancelling_the_reading_task_lets_a_waiter_read_for_itself() -> None:
    cache = ChangedFilesCache()
    tips = PullRequestTips("base", "head")
    loader = _BlockedFiles()

    async def confirm() -> PullRequestTips:
        return tips

    owner = asyncio.create_task(cache.read("acme/app", 7, tips, 1, loader.load, confirm))
    assert await asyncio.to_thread(loader.started.wait, 5)
    waiter = asyncio.create_task(cache.read("acme/app", 7, tips, 1, loader.load, confirm))
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    loader.release.set()
    assert len(await waiter) == 1
    assert loader.calls == 2
