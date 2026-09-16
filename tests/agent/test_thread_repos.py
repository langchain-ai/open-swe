from agent.run_config import Repo
from agent.thread_repos import (
    repos_metadata,
    thread_repo_full_names,
    thread_repos,
    with_thread_repo,
)


def test_repos_metadata_is_what_thread_repos_reads_back():
    repos = [Repo(owner="acme", name="oss"), Repo(owner="acme", name="api")]
    metadata = {"repos": repos_metadata(repos)}
    assert thread_repos(metadata) == repos
    assert thread_repo_full_names(metadata) == ["acme/oss", "acme/api"]


def test_legacy_single_repo_metadata_still_reads():
    """Threads written before ``repos`` carry the owner and name as flat keys."""
    assert thread_repos({"repo_owner": "acme", "repo_name": "oss"}) == [
        Repo(owner="acme", name="oss")
    ]
    assert thread_repos({"repo": {"owner": "acme", "name": "oss"}}) == [
        Repo(owner="acme", name="oss")
    ]
    assert thread_repos({}) == []


def test_with_thread_repo_appends_once():
    metadata = {"repos": repos_metadata([Repo(owner="acme", name="oss")])}
    added = with_thread_repo(metadata, Repo(owner="acme", name="api"))
    assert added == [Repo(owner="acme", name="oss"), Repo(owner="acme", name="api")]
    assert with_thread_repo(metadata, Repo(owner="ACME", name="OSS")) == [
        Repo(owner="acme", name="oss")
    ]
