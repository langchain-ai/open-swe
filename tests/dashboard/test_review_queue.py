from typing import Any

import pytest
from fastapi import HTTPException

import agent.dashboard.review_queue as review_queue
from agent.dashboard.review_queue import (
    get_review_queue,
    get_review_queue_repos,
    set_review_queue_repos,
)
from agent.dashboard.user_data import REVIEW_QUEUE_REPOS, ReviewQueueRepo
from tests.conftest import FakeStore


def _repos(*names: str) -> list[ReviewQueueRepo]:
    return [ReviewQueueRepo(full_name=name) for name in names]


def _pull(
    repo: str,
    number: int,
    *,
    mergeable: str = "MERGEABLE",
    rollup: str | None = "SUCCESS",
    has_rollup: bool = True,
    is_draft: bool = False,
    review_decision: str | None = None,
    author: str | None = "someone",
    file_paths: list[str] | None = None,
    total_files: int | None = None,
) -> dict[str, Any]:
    paths = file_paths if file_paths is not None else [f"file{number}.py"]
    return {
        "number": number,
        "title": f"{repo}#{number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "isDraft": is_draft,
        "mergeable": mergeable,
        "reviewDecision": review_decision,
        "updatedAt": "2026-01-01T00:00:00Z",
        "additions": number * 10,
        "deletions": number,
        "changedFiles": number + 1,
        "author": {"login": author} if author else None,
        "files": {
            "totalCount": total_files if total_files is not None else len(paths),
            "nodes": [{"path": path} for path in paths],
        },
        "repository": {"nameWithOwner": repo},
        "commits": {
            "nodes": [{"commit": {"statusCheckRollup": {"state": rollup} if has_rollup else None}}]
        },
    }


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _search_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    search = data.get("search") if isinstance(data, dict) else None
    nodes = search.get("nodes") if isinstance(search, dict) else None
    return nodes if isinstance(nodes, list) else []


def _patch_github(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    calls: list[dict[str, Any]],
    files_payload: dict[str, Any] | None = None,
) -> None:
    """Answer both review queue phases; ``files`` on a node feeds the second one."""
    files = {
        (node["repository"]["nameWithOwner"].lower(), node["number"]): node.pop("files")
        for node in _search_nodes(payload)
        if "files" in node
    }

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

    def client(**_kwargs: object) -> _Client:
        return _Client()

    async def request(_client: object, _method: str, _url: str, *, json: Any, **_kwargs: object):
        calls.append(json)
        if "ReviewQueueFiles" not in json["query"]:
            return _Response(payload)
        if files_payload is not None:
            return _Response(files_payload)
        variables = json["variables"]
        data: dict[str, Any] = {}
        for index in range(len(variables) // 3):
            repo = f"{variables[f'o{index}']}/{variables[f'r{index}']}".lower()
            node_files = files.get((repo, variables[f"n{index}"]))
            data[f"p{index}"] = {"pullRequest": {"files": node_files} if node_files else None}
        return _Response({"data": data})

    monkeypatch.setattr(review_queue, "github_client", client)
    monkeypatch.setattr(review_queue, "github_request", request)


def _files_requests(calls: list[dict[str, Any]]) -> list[list[tuple[str, int]]]:
    requested: list[list[tuple[str, int]]] = []
    for call in calls:
        if "ReviewQueueFiles" not in call["query"]:
            continue
        variables = call["variables"]
        requested.append(
            [
                (f"{variables[f'o{index}']}/{variables[f'r{index}']}", variables[f"n{index}"])
                for index in range(len(variables) // 3)
            ]
        )
    return requested


@pytest.fixture(autouse=True)
def _clear_cache_and_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    review_queue._cache.clear()

    async def no_review(_owner: str, _repo: str, _number: int) -> None:
        return None

    monkeypatch.setattr(review_queue, "review_summary_for_pull", no_review)
    monkeypatch.setattr(review_queue, "get_valid_access_token", _token)


async def _token(_login: str) -> str:
    return "gho_test"


async def test_keeps_only_ready_pulls_and_maps_fields(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha", "acme/beta"))
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "issueCount": 314,
                    "nodes": [
                        _pull("acme/alpha", 1, review_decision="REVIEW_REQUIRED"),
                        _pull("acme/beta", 2, has_rollup=False, author=None),
                        {},
                        _pull("acme/alpha", 3, is_draft=True),
                        _pull("acme/alpha", 4, mergeable="CONFLICTING"),
                        _pull("acme/alpha", 5, mergeable="UNKNOWN"),
                        _pull("acme/alpha", 6, rollup="FAILURE"),
                        _pull("acme/alpha", 7, rollup="PENDING"),
                    ],
                }
            }
        },
        calls,
    )

    payload = await get_review_queue("octocat")

    assert payload.total_open == 314

    assert [(item.repo_full_name, item.number) for item in payload.items] == [
        ("acme/alpha", 1),
        ("acme/beta", 2),
    ]
    first, second = payload.items
    assert (first.owner, first.repo, first.author) == ("acme", "alpha", "someone")
    assert (first.additions, first.deletions, first.changed_files) == (10, 1, 2)
    assert first.review_decision == "REVIEW_REQUIRED"
    assert first.ai_review is None
    assert (first.matched_paths, first.files_truncated) == ([], False)
    # A PR with no checks configured still counts as ready, and a missing author is null.
    assert second.author is None

    search = calls[0]["variables"]["q"]
    assert "repo:acme/alpha" in search
    assert "repo:acme/beta" in search
    assert "-author:@me" in search
    assert "files" not in calls[0]["query"]
    assert [repo.full_name for repo in payload.repos] == ["acme/alpha", "acme/beta"]


async def test_files_are_not_fetched_when_no_repo_has_paths(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha"))
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {"data": {"search": {"nodes": [_pull("acme/alpha", 1), _pull("acme/alpha", 2)]}}},
        calls,
    )

    payload = await get_review_queue("octocat")

    assert [item.number for item in payload.items] == [1, 2]
    assert [call["query"].strip().startswith("query ReviewQueueSearch") for call in calls] == [True]


async def test_files_are_fetched_only_for_ready_pulls_of_path_filtered_repos(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos(
        "octocat",
        [
            ReviewQueueRepo(full_name="acme/alpha", paths=["ui/"]),
            ReviewQueueRepo(full_name="acme/beta"),
        ],
    )
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull("acme/alpha", 1, file_paths=["ui/src/a.tsx"]),
                        _pull("acme/alpha", 2, rollup="FAILURE"),
                        _pull("acme/alpha", 3, is_draft=True),
                        _pull("acme/beta", 4),
                        _pull("acme/alpha", 5, file_paths=["ui/src/b.tsx"]),
                    ]
                }
            }
        },
        calls,
    )

    payload = await get_review_queue("octocat")

    assert [item.number for item in payload.items] == [1, 4, 5]
    assert _files_requests(calls) == [[("acme/alpha", 1), ("acme/alpha", 5)]]


async def test_files_are_batched_twenty_pulls_at_a_time(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos(
        "octocat", [ReviewQueueRepo(full_name="acme/alpha", paths=["ui/"])]
    )
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull("acme/alpha", number, file_paths=["ui/a.tsx"])
                        for number in range(1, 26)
                    ]
                }
            }
        },
        calls,
    )

    payload = await get_review_queue("octocat")

    assert len(payload.items) == 25
    assert [len(batch) for batch in _files_requests(calls)] == [20, 5]


async def test_files_phase_errors_surface_as_bad_gateway(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos(
        "octocat", [ReviewQueueRepo(full_name="acme/alpha", paths=["ui/"])]
    )
    _patch_github(
        monkeypatch,
        {"data": {"search": {"nodes": [_pull("acme/alpha", 1, file_paths=["ui/a.tsx"])]}}},
        [],
        files_payload={"errors": [{"message": "nope"}]},
    )

    with pytest.raises(HTTPException) as exc:
        await get_review_queue("octocat")

    assert exc.value.status_code == 502
    assert not review_queue._cache


async def test_ai_review_summary_is_attached(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha"))
    _patch_github(
        monkeypatch,
        {"data": {"search": {"nodes": [_pull("acme/alpha", 1)]}}},
        [],
    )

    async def summary(_owner: str, _repo: str, _number: int) -> dict[str, Any]:
        return {"status": "error", "counts": {"bugs": 2, "flags": 3, "open": 5}, "extra": "ignored"}

    monkeypatch.setattr(review_queue, "review_summary_for_pull", summary)

    payload = await get_review_queue("octocat")

    assert payload.items[0].ai_review is not None
    assert payload.items[0].ai_review.status == "error"
    assert payload.items[0].ai_review.counts.bugs == 2
    assert payload.items[0].ai_review.counts.flags == 3


async def test_empty_repo_list_skips_github(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_github(monkeypatch, {"data": {"search": {"nodes": [_pull("acme/alpha", 1)]}}}, calls)

    payload = await get_review_queue("octocat")

    assert (payload.repos, payload.items) == ([], [])
    assert not calls


async def test_github_errors_surface_as_bad_gateway(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha"))
    _patch_github(monkeypatch, {"errors": [{"message": "nope"}]}, [])

    with pytest.raises(HTTPException) as exc:
        await get_review_queue("octocat")

    assert exc.value.status_code == 502
    assert not review_queue._cache


async def test_repos_are_normalized_deduped_and_sorted(fake_store: FakeStore) -> None:
    record = await set_review_queue_repos(
        "octocat",
        [
            ReviewQueueRepo(full_name="https://github.com/acme/zeta.git"),
            ReviewQueueRepo(full_name=" acme/Alpha ", paths=["ui/"]),
            ReviewQueueRepo(full_name="acme/alpha", paths=["agent/"]),
            ReviewQueueRepo(full_name="github.com/acme/beta/"),
        ],
    )

    assert [(repo.full_name, repo.paths) for repo in record.repos] == [
        ("acme/Alpha", ["ui"]),
        ("acme/beta", []),
        ("acme/zeta", []),
    ]
    assert (await get_review_queue_repos("octocat")).repos == record.repos


async def test_paths_are_normalized_deduped_and_rejected(fake_store: FakeStore) -> None:
    record = await set_review_queue_repos(
        "octocat",
        [
            ReviewQueueRepo(
                full_name="acme/alpha",
                paths=["  ./ui/src/  ", "/agent/x.py", "ui/src", "", "   ", "/"],
            )
        ],
    )
    assert record.repos[0].paths == ["ui/src", "agent/x.py"]

    for bad in [["../secrets"], ["ui/../../etc"], ["x" * 201], [f"p{i}" for i in range(21)]]:
        with pytest.raises(HTTPException) as exc:
            await set_review_queue_repos(
                "octocat", [ReviewQueueRepo(full_name="acme/beta", paths=bad)]
            )
        assert exc.value.status_code == 400


async def test_legacy_string_records_are_upgraded_on_read(fake_store: FakeStore) -> None:
    fake_store.seed(
        REVIEW_QUEUE_REPOS.namespace("octocat"),
        REVIEW_QUEUE_REPOS.key,
        {"repos": ["acme/alpha", "acme/beta"], "updated_at": "2026-01-01T00:00:00Z"},
    )

    record = await get_review_queue_repos("octocat")

    assert [(repo.full_name, repo.paths) for repo in record.repos] == [
        ("acme/alpha", []),
        ("acme/beta", []),
    ]


async def test_invalid_and_oversized_repo_lists_are_rejected(fake_store: FakeStore) -> None:
    for bad in ["not-a-repo", "acme/..", "acme/alpha/extra", ""]:
        with pytest.raises(HTTPException) as exc:
            await set_review_queue_repos("octocat", _repos(bad))
        assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await set_review_queue_repos("octocat", _repos(*[f"acme/repo{i}" for i in range(51)]))
    assert exc.value.status_code == 400
    assert (await get_review_queue_repos("octocat")).repos == []


async def test_path_filter_keeps_only_matching_pulls(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos(
        "octocat",
        [
            ReviewQueueRepo(full_name="acme/alpha", paths=["ui/", "docs/readme.md"]),
            ReviewQueueRepo(full_name="acme/beta"),
        ],
    )
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull("acme/alpha", 1, file_paths=["ui/src/a.tsx", "docs/readme.md"]),
                        _pull("acme/alpha", 2, file_paths=["agent/x.py"]),
                        _pull("acme/alpha", 3, file_paths=["uix/a.tsx"]),
                        _pull("acme/alpha", 4, file_paths=["agent/x.py"], total_files=250),
                        _pull("acme/beta", 5, file_paths=["agent/x.py"]),
                    ]
                }
            }
        },
        [],
    )

    payload = await get_review_queue("octocat")

    assert [(item.number, item.matched_paths, item.files_truncated) for item in payload.items] == [
        (1, ["ui", "docs/readme.md"], False),
        (4, [], True),
        (5, [], False),
    ]


async def test_github_result_is_cached_per_login(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha"))
    await set_review_queue_repos("hubot", _repos("acme/alpha"))
    calls: list[dict[str, Any]] = []
    _patch_github(monkeypatch, {"data": {"search": {"nodes": [_pull("acme/alpha", 1)]}}}, calls)

    await get_review_queue("octocat")
    await get_review_queue("octocat")
    assert len(calls) == 1

    await get_review_queue("hubot")
    assert len(calls) == 2
