import httpx2

from agent.github.pull_request_diff import build_pr_diff_files

_BASE_TIP = "b" * 40
_MERGE_BASE = "m" * 40
_HEAD = "h" * 40


def _github(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    if path == "/repos/acme/app/pulls/7":
        return httpx2.Response(200, json={"base": {"sha": _BASE_TIP}, "head": {"sha": _HEAD}})
    if path == "/repos/acme/app/pulls/7/files":
        return httpx2.Response(
            200,
            json=[{"filename": "a.py", "status": "modified", "additions": 1, "deletions": 1}],
        )
    if path == f"/repos/acme/app/compare/{_BASE_TIP}...{_HEAD}":
        return httpx2.Response(200, json={"merge_base_commit": {"sha": _MERGE_BASE}})
    if path == "/repos/acme/app/contents/a.py":
        contents = {
            _MERGE_BASE: "old\n",
            _BASE_TIP: "base moved on\nold\n",
            _HEAD: "new\n",
        }
        return httpx2.Response(200, content=contents[request.url.params["ref"]].encode())
    return httpx2.Response(404)


async def test_original_content_is_read_at_the_merge_base_not_the_base_tip() -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(_github)) as client:
        diff = await build_pr_diff_files(client, "acme/app", 7)

    [file] = diff["files"]
    assert file["originalContent"] == "old\n"
    assert file["modifiedContent"] == "new\n"
    assert diff["base_sha"] == _MERGE_BASE
