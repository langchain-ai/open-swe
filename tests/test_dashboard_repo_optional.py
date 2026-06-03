from __future__ import annotations

from agent.dashboard import thread_api


def test_resolve_repo_config_parses_request_repo() -> None:
    assert thread_api._resolve_repo_config("octo/repo") == {"owner": "octo", "name": "repo"}


def test_resolve_repo_config_returns_empty_when_no_repo_given() -> None:
    # None / blank / malformed all mean an intentionally repo-less run — never an error.
    assert thread_api._resolve_repo_config(None) == {}
    assert thread_api._resolve_repo_config("") == {}
    assert thread_api._resolve_repo_config("not-a-repo") == {}


def test_thread_summary_blanks_repo_when_absent() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "title": "no repo run"}}
    )
    assert summary["repo"] == ""
    assert summary["repoFullName"] == ""


def test_thread_summary_keeps_repo_when_present() -> None:
    summary = thread_api._thread_summary(
        {
            "thread_id": "t2",
            "metadata": {
                "source": "dashboard",
                "title": "repo run",
                "repo_owner": "octo",
                "repo_name": "repo",
            },
        }
    )
    assert summary["repo"] == "repo"
    assert summary["repoFullName"] == "octo/repo"
