import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from openswe.analytics import distance
from openswe.analytics.distance import _insert_delete_distance, _patch_lines


def test_patch_lines_rejects_incomplete_text_patch():
    assert _patch_lines([{"filename": "image.png", "changes": 0}]) is None


def test_large_rewrite_exceeds_computation_budget() -> None:
    assert _insert_delete_distance(["old"] * 10_000, ["new"] * 10_000) is None


@pytest.mark.parametrize(
    ("opening_patch", "final_patch", "final_additions", "expected"),
    [
        ("+old", "+new", 1, 10_000),
        ("+same", "+same", 1, 0),
        ("+old\n" * 10_000, "+new\n" * 10_000, 10_000, None),
        ("+same", "+same", 1000, None),
    ],
)
async def test_measurement_keeps_event_loop_available(
    monkeypatch: pytest.MonkeyPatch,
    opening_patch: str,
    final_patch: str,
    final_additions: int,
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
                [
                    {
                        "filename": "a",
                        "patch": opening_patch,
                        "additions": len(opening_patch.splitlines()),
                        "deletions": 0,
                    }
                ],
                [
                    {
                        "filename": "a",
                        "patch": final_patch,
                        "additions": final_additions,
                        "deletions": 0,
                    }
                ],
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


@pytest.mark.parametrize(
    ("additions", "deletions"),
    [
        (1000, 1),
        (1, 1000),
        (0, 2),
        (2, 0),
        (None, 1),
        (1, None),
        ("1", 1),
        (True, 1),
        (-1, 1),
    ],
)
def test_patch_lines_rejects_mismatched_or_missing_statistics(
    additions: object,
    deletions: object,
) -> None:
    assert (
        _patch_lines(
            [
                {
                    "filename": "a",
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                    "additions": additions,
                    "deletions": deletions,
                }
            ]
        )
        is None
    )
