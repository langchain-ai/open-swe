from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.review_scout import launch

pytestmark = pytest.mark.usefixtures("no_local_threads")


@pytest.mark.parametrize("active", [None, "active-run"])
async def test_scout_records_security_identity_before_starting_or_reusing_a_run(
    monkeypatch: pytest.MonkeyPatch, active: str | None
) -> None:
    stored: dict[str, object] = {"title": "Walkthrough", "title_locked": True}

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        stored.update(metadata)

    threads = SimpleNamespace(
        create=AsyncMock(return_value={"metadata": stored}),
        update=AsyncMock(side_effect=update),
    )
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(launch, "dispatch_client", lambda: client)
    target = launch.ReviewScoutTarget(
        owner="acme",
        repo="api",
        pr_number=7,
        pr_title="Change",
        base_sha="base",
        head_sha="head",
        workspace_slug="workspace",
    )

    async def active_run(self: launch.ReviewScoutTarget) -> str | None:
        assert stored == {
            "title": "Walkthrough",
            "title_locked": True,
            "kind": "review-scout",
            "pr": {"owner": "acme", "name": "api", "number": 7},
        }
        return active

    monkeypatch.setattr(launch.ReviewScoutTarget, "active_run", active_run)
    monkeypatch.setattr(launch, "create_durable_run", AsyncMock(return_value={"run_id": "new-run"}))

    assert await target.start() == (active or "new-run")
