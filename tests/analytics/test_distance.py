import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from agent.analytics import distance
from agent.analytics.distance import _insert_delete_distance, _patch_lines


def test_patch_distance_ignores_context_and_diff_metadata():
    opening = _patch_lines(
        [
            {
                "filename": "agent/example.py",
                "changes": 2,
                "patch": "@@ -1,2 +1,2 @@\n-old = 1\n+new = 1\n context",
            }
        ]
    )
    final = _patch_lines(
        [
            {
                "filename": "agent/example.py",
                "changes": 2,
                "patch": "@@ -20,2 +20,2 @@\n-old = 1\n+new = 2\n context",
            }
        ]
    )

    assert opening is not None and final is not None
    assert _insert_delete_distance(opening, final) == 2


def test_patch_lines_rejects_incomplete_text_patch():
    assert _patch_lines([{"filename": "image.png", "changes": 0}]) is None


def test_patch_lines_preserves_content_with_repeated_signs():
    assert _patch_lines(
        [{"filename": "README.md", "patch": "@@ -1 +1 @@\n----\n++++", "changes": 2}]
    ) == ["README.md\0----", "README.md\0++++"]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ([], [], 0),
        ([], ["a"], 1),
        (["a"], [], 1),
        (["a"], ["a"], 0),
        (["a"], ["b"], 2),
        (["a", "b"], ["b", "a"], 2),
    ],
)
def test_exact_distance(before: list[str], after: list[str], expected: int) -> None:
    assert _insert_delete_distance(before, after) == expected


def test_large_rewrite_exceeds_computation_budget() -> None:
    assert _insert_delete_distance(["old"] * 10_000, ["new"] * 10_000) is None


def test_patch_size_budget_applies_across_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(distance, "_MAX_PATCH_CHARACTERS", 10)
    assert (
        _patch_lines(
            [
                {"filename": "a", "patch": "+12345"},
                {"filename": "b", "patch": "+12345"},
            ]
        )
        is None
    )


@pytest.mark.parametrize(
    ("opening_patch", "final_patch", "expected"),
    [
        ("+old", "+new", 10_000),
        ("+same", "+same", 0),
        ("+old\n" * 10_000, "+new\n" * 10_000, None),
    ],
)
async def test_measurement_keeps_event_loop_available(
    monkeypatch: pytest.MonkeyPatch,
    opening_patch: str,
    final_patch: str,
    expected: int | None,
) -> None:
    monkeypatch.setattr(
        distance, "get_github_app_installation_token", AsyncMock(return_value="test")
    )
    monkeypatch.setattr(distance, "github_client", Mock(return_value=AsyncMock()))
    monkeypatch.setattr(
        distance,
        "_compare_files",
        AsyncMock(
            side_effect=[
                [{"filename": "a", "patch": opening_patch}],
                [{"filename": "a", "patch": final_patch}],
            ]
        ),
    )
    # This runs only once the coroutine yields; the mocked HTTP calls do not yield.
    heartbeat = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(heartbeat.set_result, None)
    result = await distance.post_open_distance_basis_points(
        owner="owner",
        repo="repo",
        opening_base_sha="base",
        opening_head_sha="opening",
        final_base_sha="base",
        final_head_sha="final",
    )
    assert heartbeat.done()
    assert result == expected
