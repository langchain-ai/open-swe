import agent.dashboard.pull_request_checks as checks_module
from agent.dashboard.pull_request_checks import get_pull_request_check_states


def _rollup(state: str | None, pr_state: str = "OPEN", is_draft: bool = False) -> dict[str, object]:
    return {
        "pullRequest": {
            "state": pr_state,
            "isDraft": is_draft,
            "commits": {
                "nodes": [{"commit": {"statusCheckRollup": {"state": state} if state else None}}]
            },
        }
    }


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _patch_github(monkeypatch, payload, calls: list[dict[str, object]]):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    def client(**_kwargs):
        return _Client()

    async def request(_client, _method, _url, *, json, **_kwargs):
        calls.append(json)
        return _Response(payload)

    monkeypatch.setattr(checks_module, "github_client", client)
    monkeypatch.setattr(checks_module, "github_request", request)


async def test_maps_rollup_states_and_skips_invalid_records(monkeypatch):
    checks_module._cache.clear()
    calls: list[dict[str, object]] = []
    _patch_github(
        monkeypatch,
        {"data": {"p0": _rollup("FAILURE"), "p1": _rollup("SUCCESS"), "p2": _rollup(None)}},
        calls,
    )

    result = await get_pull_request_check_states(
        [
            {"repoFullName": "acme/alpha", "number": 1},
            {"repoFullName": "acme/beta", "number": 2},
            {"repoFullName": "acme/gamma", "number": 3},
            {"repoFullName": "acme/../etc", "number": 4},
            {"repoFullName": "no-slash", "number": 5},
        ],
        "octocat",
        "token",
    )

    assert result == {
        "acme/alpha#1": {"checks": "failing", "state": "open"},
        "acme/beta#2": {"checks": "passing", "state": "open"},
        "acme/gamma#3": {"checks": "passing", "state": "open"},
    }
    # Invalid identities never reach GitHub.
    assert len(calls) == 1
    assert "etc" not in str(calls[0]["variables"])


async def test_caches_per_login(monkeypatch):
    checks_module._cache.clear()
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"data": {"p0": _rollup("FAILURE")}}, calls)
    record = [{"repoFullName": "acme/alpha", "number": 1}]

    expected = {"acme/alpha#1": {"checks": "failing", "state": "open"}}
    assert await get_pull_request_check_states(record, "octocat", "token") == expected
    assert await get_pull_request_check_states(record, "octocat", "token") == expected
    assert len(calls) == 1

    await get_pull_request_check_states(record, "someone-else", "token")
    assert len(calls) == 2


async def test_returns_unknown_when_github_fails(monkeypatch):
    checks_module._cache.clear()
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"errors": [{"message": "nope"}]}, calls)

    result = await get_pull_request_check_states(
        [{"repoFullName": "acme/alpha", "number": 1}], "octocat", "token"
    )

    assert result == {"acme/alpha#1": {"checks": "unknown", "state": None}}
    assert not checks_module._cache


async def test_reports_merged_and_draft_state(monkeypatch):
    """The sidebar renders from this, so a merged PR must stop reading as open."""
    checks_module._cache.clear()
    calls: list[dict[str, object]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "p0": _rollup("SUCCESS", pr_state="MERGED"),
                "p1": _rollup("PENDING", is_draft=True),
                "p2": _rollup(None, pr_state="CLOSED"),
            }
        },
        calls,
    )

    result = await get_pull_request_check_states(
        [
            {"repoFullName": "acme/alpha", "number": 1},
            {"repoFullName": "acme/beta", "number": 2},
            {"repoFullName": "acme/gamma", "number": 3},
        ],
        "octocat",
        "token",
    )

    assert result["acme/alpha#1"]["state"] == "merged"
    assert result["acme/beta#2"]["state"] == "draft"
    assert result["acme/gamma#3"]["state"] == "closed"
