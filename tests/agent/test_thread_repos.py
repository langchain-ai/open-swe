from agent.thread_repos import (
    parse_repository_ids,
    repository_ids_metadata,
    thread_repositories,
    thread_repository_ids,
)
from tests.support.repositories import FakeRepositories


async def test_repository_ids_metadata_is_what_thread_repositories_reads_back(
    fake_repositories: FakeRepositories,
) -> None:
    rows = [fake_repositories.add("acme/oss"), fake_repositories.add("acme/api")]
    metadata = {"repository_ids": repository_ids_metadata(rows)}
    assert await thread_repositories(metadata) == rows
    assert thread_repository_ids(metadata) == [row.id for row in rows]


async def test_legacy_single_repo_metadata_still_resolves(
    fake_repositories: FakeRepositories,
) -> None:
    """Threads written before ``repository_ids`` carry the owner and name inline."""
    by_flat_keys = await thread_repositories({"repo_owner": "acme", "repo_name": "oss"})
    assert [row.full_name for row in by_flat_keys] == ["acme/oss"]
    by_repo_dict = await thread_repositories({"repo": {"owner": "acme", "name": "oss"}})
    assert by_repo_dict == by_flat_keys
    assert await thread_repositories({}) == []


def test_repository_ids_metadata_dedupes_and_keeps_order(
    fake_repositories: FakeRepositories,
) -> None:
    oss = fake_repositories.add("acme/oss")
    api = fake_repositories.add("acme/api")
    assert repository_ids_metadata([oss, api, oss]) == [str(oss.id), str(api.id)]


def test_parse_repository_ids_drops_malformed_values(
    fake_repositories: FakeRepositories,
) -> None:
    oss = fake_repositories.add("acme/oss")
    assert parse_repository_ids([str(oss.id), "not-a-uuid", str(oss.id)]) == [oss.id]
    assert parse_repository_ids("acme/oss") == []
