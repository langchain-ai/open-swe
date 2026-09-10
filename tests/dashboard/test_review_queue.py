from typing import Any

import pytest
from fastapi import HTTPException

import agent.dashboard.review_queue as review_queue
from agent.dashboard.review_queue import (
    get_review_queue,
    get_review_queue_repos,
    set_review_queue_repos,
)
from agent.dashboard.user_data import (
    REVIEW_QUEUE_REPOS,
    ReviewQueueChecksMode,
    ReviewQueueRepo,
)
from tests.conftest import FakeStore


def _repos(*names: str, checks: ReviewQueueChecksMode = "required") -> list[ReviewQueueRepo]:
    return [ReviewQueueRepo(full_name=name, checks=checks) for name in names]


def _check(
    name: str, conclusion: str | None, *, required: bool, status: str = "COMPLETED"
) -> dict[str, Any]:
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "isRequired": required,
    }


def _status(context: str, state: str, *, required: bool) -> dict[str, Any]:
    return {
        "__typename": "StatusContext",
        "context": context,
        "state": state,
        "isRequired": required,
    }


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
    contexts: list[dict[str, Any]] | None = None,
    total_contexts: int | None = None,
) -> dict[str, Any]:
    paths = file_paths if file_paths is not None else [f"file{number}.py"]
    nodes = contexts if contexts is not None else []
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
        "contexts": {
            "totalCount": total_contexts if total_contexts is not None else len(nodes),
            "nodes": nodes,
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


def _alias_selection(query: str, index: int) -> str:
    """The slice of a phase-2 query that belongs to alias ``p{index}``."""
    start = query.index(f"p{index}: repository")
    end = query.find(f"p{index + 1}: repository", start)
    return query[start:] if end < 0 else query[start:end]


def _patch_github(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    calls: list[dict[str, Any]],
    details_payload: dict[str, Any] | None = None,
) -> None:
    """Answer both review queue phases; ``files``/``contexts`` on a node feed the second one."""
    details = {
        (node["repository"]["nameWithOwner"].lower(), node["number"]): {
            "files": node.pop("files", None),
            "contexts": node.pop("contexts", None),
        }
        for node in _search_nodes(payload)
        if "files" in node or "contexts" in node
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
        if "ReviewQueueDetails" not in json["query"]:
            return _Response(payload)
        if details_payload is not None:
            return _Response(details_payload)
        variables = json["variables"]
        data: dict[str, Any] = {}
        for index in range(len(variables) // 3):
            repo = f"{variables[f'o{index}']}/{variables[f'r{index}']}".lower()
            node = details.get((repo, variables[f"n{index}"]))
            if node is None:
                data[f"p{index}"] = {"pullRequest": None}
                continue
            selection = _alias_selection(json["query"], index)
            pull_request: dict[str, Any] = {}
            if node["files"] is not None and "files(first:" in selection:
                pull_request["files"] = node["files"]
            if node["contexts"] is not None and "statusCheckRollup" in selection:
                pull_request["commits"] = {
                    "nodes": [
                        {
                            "commit": {
                                "statusCheckRollup": {
                                    "state": "SUCCESS",
                                    "contexts": node["contexts"],
                                }
                            }
                        }
                    ]
                }
            data[f"p{index}"] = {"pullRequest": pull_request}
        return _Response({"data": data})

    monkeypatch.setattr(review_queue, "github_client", client)
    monkeypatch.setattr(review_queue, "github_request", request)


def _details_requests(calls: list[dict[str, Any]]) -> list[list[tuple[str, int]]]:
    requested: list[list[tuple[str, int]]] = []
    for call in calls:
        if "ReviewQueueDetails" not in call["query"]:
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
    assert (first.matched_paths, first.files_truncated, first.optional_failures) == ([], False, 0)
    # A PR with no checks configured still counts as ready, and a missing author is null.
    assert second.author is None

    search = calls[0]["variables"]["q"]
    assert "repo:acme/alpha" in search
    assert "repo:acme/beta" in search
    assert "-author:@me" in search
    assert "files" not in calls[0]["query"]
    assert [repo.full_name for repo in payload.repos] == ["acme/alpha", "acme/beta"]


async def test_details_are_not_fetched_when_nothing_needs_them(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha", checks="ignore"))
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {"data": {"search": {"nodes": [_pull("acme/alpha", 1), _pull("acme/alpha", 2)]}}},
        calls,
    )

    payload = await get_review_queue("octocat")

    assert [item.number for item in payload.items] == [1, 2]
    assert [call["query"].strip().startswith("query ReviewQueueSearch") for call in calls] == [True]


async def test_details_are_fetched_only_for_ready_pulls_that_need_them(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos(
        "octocat",
        [
            ReviewQueueRepo(full_name="acme/alpha", paths=["ui/"], checks="all"),
            ReviewQueueRepo(full_name="acme/beta", checks="all"),
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
    assert _details_requests(calls) == [[("acme/alpha", 1), ("acme/alpha", 5)]]


async def test_details_are_batched_twenty_pulls_at_a_time(
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
    assert [len(batch) for batch in _details_requests(calls)] == [20, 5]


async def test_details_phase_errors_surface_as_bad_gateway(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos(
        "octocat", [ReviewQueueRepo(full_name="acme/alpha", paths=["ui/"])]
    )
    _patch_github(
        monkeypatch,
        {"data": {"search": {"nodes": [_pull("acme/alpha", 1, file_paths=["ui/a.tsx"])]}}},
        [],
        details_payload={"errors": [{"message": "nope"}]},
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


async def test_legacy_records_are_upgraded_on_read(fake_store: FakeStore) -> None:
    fake_store.seed(
        REVIEW_QUEUE_REPOS.namespace("octocat"),
        REVIEW_QUEUE_REPOS.key,
        {
            "repos": ["acme/alpha", {"full_name": "acme/beta", "paths": ["ui"]}],
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )

    record = await get_review_queue_repos("octocat")

    assert [(repo.full_name, repo.paths, repo.checks) for repo in record.repos] == [
        ("acme/alpha", [], "required"),
        ("acme/beta", ["ui"], "required"),
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


async def test_required_mode_ignores_checks_that_are_not_required(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha"))
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull(
                            "acme/alpha",
                            1,
                            rollup="FAILURE",
                            contexts=[
                                _check("ci", "SUCCESS", required=True),
                                _check("lint", "FAILURE", required=False),
                                _status("coverage", "ERROR", required=False),
                                _check("flaky", "CANCELLED", required=False),
                                _check("nightly", None, required=False, status="IN_PROGRESS"),
                            ],
                        )
                    ]
                }
            }
        },
        [],
    )

    payload = await get_review_queue("octocat")

    assert [(item.number, item.optional_failures) for item in payload.items] == [(1, 3)]


async def test_required_mode_hides_failing_pending_and_unknown_required_checks(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha"))
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull("acme/alpha", 1, contexts=[_check("ci", "FAILURE", required=True)]),
                        _pull(
                            "acme/alpha",
                            2,
                            contexts=[_check("ci", None, required=True, status="IN_PROGRESS")],
                        ),
                        _pull(
                            "acme/alpha", 3, contexts=[_status("legacy", "ERROR", required=True)]
                        ),
                        _pull(
                            "acme/alpha",
                            4,
                            contexts=[_check("ci", "SUCCESS", required=True)],
                            total_contexts=250,
                        ),
                        _pull(
                            "acme/alpha",
                            5,
                            contexts=[
                                _check("ci", "SUCCESS", required=True),
                                _check("optional-suite", "SKIPPED", required=True),
                                _check("advisory", "NEUTRAL", required=True),
                                _status("legacy", "SUCCESS", required=True),
                            ],
                        ),
                    ]
                }
            }
        },
        [],
    )

    payload = await get_review_queue("octocat")

    assert [item.number for item in payload.items] == [5]


async def test_all_mode_filters_on_the_rollup_without_a_details_query(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha", checks="all"))
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull("acme/alpha", 1),
                        _pull(
                            "acme/alpha",
                            2,
                            rollup="FAILURE",
                            contexts=[_check("lint", "FAILURE", required=False)],
                        ),
                        _pull("acme/alpha", 3, rollup="PENDING"),
                        _pull("acme/alpha", 4, has_rollup=False),
                    ]
                }
            }
        },
        calls,
    )

    payload = await get_review_queue("octocat")

    assert [(item.number, item.optional_failures) for item in payload.items] == [(1, 0), (4, 0)]
    assert _details_requests(calls) == []


async def test_ignore_mode_keeps_pulls_whose_required_checks_failed(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha", checks="ignore"))
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull(
                            "acme/alpha",
                            1,
                            rollup="FAILURE",
                            contexts=[
                                _check("ci", "FAILURE", required=True),
                                _check("lint", "FAILURE", required=False),
                            ],
                        )
                    ]
                }
            }
        },
        calls,
    )

    payload = await get_review_queue("octocat")

    assert [(item.number, item.optional_failures) for item in payload.items] == [(1, 0)]
    assert _details_requests(calls) == []


async def test_details_query_selects_only_what_each_repo_needs(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos(
        "octocat",
        [
            ReviewQueueRepo(full_name="acme/alpha", paths=["ui/"]),
            ReviewQueueRepo(full_name="acme/beta", paths=["ui/"], checks="all"),
            ReviewQueueRepo(full_name="acme/gamma"),
            ReviewQueueRepo(full_name="acme/delta", checks="ignore"),
        ],
    )
    calls: list[dict[str, Any]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "search": {
                    "nodes": [
                        _pull(
                            "acme/alpha",
                            1,
                            file_paths=["ui/a.tsx"],
                            contexts=[_check("ci", "SUCCESS", required=True)],
                        ),
                        _pull("acme/beta", 2, file_paths=["ui/b.tsx"]),
                        _pull("acme/gamma", 3, contexts=[_check("ci", "SUCCESS", required=True)]),
                        _pull("acme/delta", 4),
                    ]
                }
            }
        },
        calls,
    )

    payload = await get_review_queue("octocat")

    assert [item.number for item in payload.items] == [1, 2, 3, 4]
    assert _details_requests(calls) == [[("acme/alpha", 1), ("acme/beta", 2), ("acme/gamma", 3)]]

    query = next(call["query"] for call in calls if "ReviewQueueDetails" in call["query"])
    alpha, beta, gamma = (_alias_selection(query, index) for index in range(3))
    assert "files(first: 100)" in alpha
    assert "isRequired(pullRequestNumber: $n0)" in alpha
    assert "files(first: 100)" in beta
    assert "statusCheckRollup" not in beta
    assert "files" not in gamma
    assert "isRequired(pullRequestNumber: $n2)" in gamma


async def test_github_result_is_cached_per_login(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    await set_review_queue_repos("octocat", _repos("acme/alpha"))
    await set_review_queue_repos("hubot", _repos("acme/alpha"))
    calls: list[dict[str, Any]] = []
    _patch_github(monkeypatch, {"data": {"search": {"nodes": [_pull("acme/alpha", 1)]}}}, calls)

    await get_review_queue("octocat")
    fetched = len(calls)
    assert fetched

    await get_review_queue("octocat")
    assert len(calls) == fetched

    await get_review_queue("hubot")
    assert len(calls) > fetched

    cached = len(calls)
    await set_review_queue_repos("octocat", _repos("acme/alpha", checks="ignore"))
    await get_review_queue("octocat")
    assert len(calls) > cached
