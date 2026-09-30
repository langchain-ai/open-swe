"""Repository rows remember GitHub's numeric id and when it was last looked up."""

from datetime import UTC, datetime

import pytest

from agent.github.repositories import Repository

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_recorded_github_id_is_read_back_by_key() -> None:
    await Repository(full_name="Acme/API").save()
    checked_at = datetime.now(UTC)

    await Repository.record_github_ids({"acme/api": 11}, checked_at=checked_at)

    rows = await Repository.by_keys(["acme/api", "acme/unknown"])
    assert list(rows) == ["acme/api"]
    assert rows["acme/api"].github_id == 11
    assert rows["acme/api"].github_checked_at == checked_at


async def test_recording_a_missing_id_clears_it_and_only_touches_known_rows() -> None:
    await Repository(full_name="acme/api").save()
    await Repository.record_github_ids({"acme/api": 11}, checked_at=datetime.now(UTC))
    checked_at = datetime.now(UTC)

    await Repository.record_github_ids(
        {"acme/api": None, "acme/unknown": 22}, checked_at=checked_at
    )

    rows = await Repository.by_keys(["acme/api", "acme/unknown"])
    assert list(rows) == ["acme/api"]
    assert rows["acme/api"].github_id is None
    assert rows["acme/api"].github_checked_at == checked_at


async def test_saving_a_repository_keeps_its_github_id() -> None:
    await Repository(full_name="acme/api").save()
    await Repository.record_github_ids({"acme/api": 11}, checked_at=datetime.now(UTC))

    await Repository(full_name="acme/api", default_branch="main").save()

    assert (await Repository.by_keys(["acme/api"]))["acme/api"].github_id == 11
