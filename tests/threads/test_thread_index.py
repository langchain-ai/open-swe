from datetime import UTC, datetime

from agent.review.session import REVIEW_CHAT_SOURCE
from agent.threads.index import (
    delete_thread_index,
    derive_thread_index_row,
    load_thread_index_row,
    upsert_thread_index,
)
from agent.utils.json_types import JsonObject

CREATED_MS = 1_750_000_000_000
UPDATED_MS = 1_750_000_100_000


def _thread(metadata: JsonObject, *, status: str = "idle") -> JsonObject:
    return {
        "thread_id": "thread-1",
        "status": status,
        "metadata": metadata,
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-02T00:00:00Z",
    }


def _ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, UTC)


def _dashboard_metadata(**overrides: object) -> JsonObject:
    return {
        "source": "dashboard",
        "origin": "dashboard",
        "owner_type": "user",
        "owner_login": "Octocat",
        "visibility": "public",
        "thread_category": "interactive",
        "trigger_kind": "user",
        "participant_logins": {"octocat": True},
        "participant_emails": {"octo@example.com": True},
        "title": "Fix the flaky test",
        "repo_owner": "LangChain-AI",
        "repo_name": "Open-SWE",
        "workspace": "default",
        "created_at_ms": CREATED_MS,
        "updated_at_ms": UPDATED_MS,
        **overrides,
    }


def test_dashboard_thread() -> None:
    row = derive_thread_index_row(
        _thread(
            _dashboard_metadata(
                latest_run_id="run-2", latest_run_status="success", last_viewed_run_id="run-1"
            )
        )
    )

    assert row.listed is True
    assert row.participants == ["octo@example.com", "octocat"]
    assert row.category == "interactive"
    assert row.source == "dashboard"
    assert row.status == "finished"
    assert row.latest_run_id == "run-2"
    assert row.viewed is False
    assert row.resolved is False
    assert row.visibility == "public"
    assert row.owner_login == "octocat"
    assert row.reader_login is None
    assert row.repo_full_name == "langchain-ai/open-swe"
    assert row.workspace == "default"
    assert row.title == "Fix the flaky test"
    assert row.created_at == _ms(CREATED_MS)
    assert row.updated_at == _ms(UPDATED_MS)
    assert row.metadata["title"] == "Fix the flaky test"


def test_busy_thread_that_was_viewed_and_resolved() -> None:
    row = derive_thread_index_row(
        _thread(
            _dashboard_metadata(
                latest_run_id="run-1",
                latest_run_status="success",
                last_viewed_run_id="run-1",
                resolved=True,
            ),
            status="busy",
        )
    )

    assert row.status == "running"
    assert row.thread_status == "busy"
    assert row.viewed is True
    assert row.resolved is True


def test_automation_thread() -> None:
    row = derive_thread_index_row(
        _thread(
            {
                "source": "schedule",
                "origin": "schedule",
                "thread_category": "automation",
                "trigger_kind": "schedule",
                "schedule_id": "sched-1",
                "owner_type": "system",
                "visibility": "public",
                "title": "Scheduled: triage",
                "repo_owner": "acme",
                "repo_name": "api",
                "admin_thread": True,
                "latest_run_status": "error",
                "created_at_ms": CREATED_MS,
                "updated_at_ms": UPDATED_MS,
            }
        )
    )

    assert row.listed is True
    assert row.category == "automation"
    assert row.schedule_id == "sched-1"
    assert row.admin_thread is True
    assert row.participants == []
    assert row.owner_login is None
    assert row.status == "error"


def test_legacy_automation_without_category_is_classified() -> None:
    row = derive_thread_index_row(_thread({"source": "schedule", "schedule_id": "sched-1"}))

    assert row.category == "automation"


def test_webhook_pull_request_thread() -> None:
    row = derive_thread_index_row(
        _thread(
            {
                "source": "github",
                "origin": "github",
                "thread_category": "pull_request",
                "repo": {"owner": "acme", "name": "api"},
                "repo_owner": "acme",
                "repo_name": "api",
                "participant_logins": {"alice": True},
                "source_context": {"pr_number": 12},
                "visibility": "public",
                "owner_type": "user",
                "owner_login": "alice",
                "created_at_ms": CREATED_MS,
                "updated_at_ms": UPDATED_MS,
            }
        )
    )

    assert row.listed is True
    assert row.category == "pull_request"
    assert row.participants == ["alice"]
    assert row.repo_full_name == "acme/api"


def test_webhook_issue_thread_without_category_is_classified() -> None:
    row = derive_thread_index_row(
        _thread(
            {
                "source": "linear",
                "participant_emails": {"Bob@Example.com": True},
                "source_context": {"linear_issue": {"id": "iss", "identifier": "ENG-1"}},
            }
        )
    )

    assert row.listed is True
    assert row.category == "issue"
    assert row.participants == ["bob@example.com"]
    assert row.repo_full_name is None


def test_review_chat_is_exclusive_to_its_reader() -> None:
    row = derive_thread_index_row(
        _thread(
            {
                "source": REVIEW_CHAT_SOURCE,
                "github_login": "Reviewer",
                "repo_owner": "acme",
                "repo_name": "api",
                "pr_number": 7,
                "participant_logins": {"reviewer": True},
                "thread_category": "review",
                "walkthrough_state": "building",
                "created_at_ms": CREATED_MS,
                "updated_at_ms": UPDATED_MS,
            }
        )
    )

    assert row.listed is True
    assert row.reader_login == "reviewer"
    assert row.participants == ["reviewer"]
    assert row.category == "review"
    assert row.status == "running"


def test_review_chat_with_unseen_walkthrough_is_unread() -> None:
    row = derive_thread_index_row(
        _thread(
            {
                "source": REVIEW_CHAT_SOURCE,
                "github_login": "reviewer",
                "repo_owner": "acme",
                "repo_name": "api",
                "pr_number": 7,
                "walkthrough_state": "ready",
                "walkthrough_ready_at_ms": UPDATED_MS,
                "last_viewed_at_ms": CREATED_MS,
            }
        )
    )

    assert row.status == "finished"
    assert row.viewed is False


def test_legacy_thread_with_only_github_login() -> None:
    row = derive_thread_index_row(
        _thread({"source": "slack", "github_login": "Carol", "triggering_user_email": "C@x.io"})
    )

    assert row.listed is True
    assert row.participants == ["c@x.io", "carol"]
    assert row.reader_login is None


def test_private_thread() -> None:
    row = derive_thread_index_row(
        _thread(_dashboard_metadata(visibility="private", admin_thread=True))
    )

    assert row.listed is True
    assert row.visibility == "private"
    assert row.owner_login == "octocat"
    assert row.admin_thread is True


def test_unlisted_thread_is_not_listed() -> None:
    row = derive_thread_index_row(_thread({"source": "slack", "unlisted": True}))

    assert row.listed is False


def test_incidents_agent_thread_is_not_listed() -> None:
    row = derive_thread_index_row(_thread({"source": "incidents_agent"}))

    assert row.listed is False


def test_timestamps_fall_back_to_langgraph_columns() -> None:
    row = derive_thread_index_row(_thread({}))

    assert row.source == "dashboard"
    assert row.listed is True
    assert row.status == "idle"
    assert row.created_at == datetime(2025, 1, 1, tzinfo=UTC)
    assert row.updated_at == datetime(2025, 1, 2, tzinfo=UTC)


async def test_upsert_load_delete_round_trip(registry_db: None) -> None:
    thread = _thread(_dashboard_metadata(latest_run_status="running"))
    await upsert_thread_index(thread)
    stored = await load_thread_index_row("thread-1")
    assert stored == derive_thread_index_row(thread)

    updated = _thread(_dashboard_metadata(resolved=True, title="Renamed"))
    await upsert_thread_index(updated)
    stored = await load_thread_index_row("thread-1")
    assert stored == derive_thread_index_row(updated)

    assert await delete_thread_index("thread-1") is True
    assert await load_thread_index_row("thread-1") is None
    assert await delete_thread_index("thread-1") is False
