"""Unit tests for the Finding schema and its PostgreSQL-backed storage."""

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from agent.database import postgres
from agent.review.findings import (
    SEVERITY_ORDER,
    DiffSide,
    Finding,
    FindingRow,
    InteractionRow,
    ReviewerThreadMissingError,
    ReviewerThreadUnlinkedError,
    append_finding,
    append_finding_interaction,
    comment_ids_for_finding,
    filter_findings_for_publish,
    findings_by_thread,
    is_surfaced,
    is_thread_resolved,
    list_findings,
    mutate_findings,
    new_finding,
    new_finding_id,
    replace_findings,
    resolve_review_head_sha,
    review_id_for_finding,
    set_reviewer_thread_metadata,
    surface_state_of,
    thread_ids_for_finding,
    update_finding_fields,
)
from agent.run_config import RunConfig


def _f(**overrides: Any) -> Finding:
    base = new_finding(
        severity="high",
        confidence="high",
        category="correctness",
        file="foo.py",
        start_line=10,
        end_line=10,
        description="boom",
        sha="abc123",
    )
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _metadata_client(metadata: dict[str, Any] | None = None, *, number: int = 1) -> AsyncMock:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {"pr": {"owner": "acme", "name": "app", "number": number}, **(metadata or {})}
    }
    return client


def _not_found(method: str = "GET") -> Exception:
    import httpx2
    from langgraph_sdk.errors import NotFoundError

    return NotFoundError(
        "thread tid not found",
        response=httpx2.Response(404, request=httpx2.Request(method, "http://x")),
        body=None,
    )


def test_new_finding_id_format() -> None:
    fid = new_finding_id()
    assert fid.startswith("f_")
    assert len(fid) == len("f_") + 10


def test_new_finding_defaults() -> None:
    finding = _f()
    assert finding["status"] == "open"
    assert finding["side"] == "RIGHT"
    assert finding["first_seen_sha"] == "abc123"
    assert finding["last_confirmed_sha"] == "abc123"
    assert finding["github_review_id"] is None
    assert finding["github_review_comment_ids"] == []
    assert finding["github_review_thread_ids"] == []
    assert finding["github_review_run_id"] is None
    assert finding["github_resolved_thread_ids"] == []
    assert finding["github_posted_resolution_comment_ids"] == []
    assert finding["surface_state"] == "not_surfaced"
    assert finding["last_human_reply_at"] is None
    assert finding["resolution_note"] is None
    assert finding["suggestion"] is None
    assert finding["rank"] is None


def test_fingerprint_covers_side_and_full_description() -> None:
    prefix = "x" * 200

    def _with(*, side: DiffSide, description: str) -> Finding:
        return new_finding(
            severity="high",
            confidence="high",
            category="correctness",
            file="foo.py",
            start_line=10,
            end_line=10,
            description=description,
            sha="abc123",
            side=side,
        )

    right = _with(side="RIGHT", description=f"{prefix} one")
    left = _with(side="LEFT", description=f"{prefix} one")
    different_suffix = _with(side="RIGHT", description=f"{prefix} two")

    assert right["fingerprint"] != left["fingerprint"]
    assert right["fingerprint"] != different_suffix["fingerprint"]


def test_severity_order_monotonic() -> None:
    assert (
        SEVERITY_ORDER["low"]
        < SEVERITY_ORDER["medium"]
        < SEVERITY_ORDER["high"]
        < SEVERITY_ORDER["critical"]
    )


def test_filter_findings_for_publish_drops_below_threshold_and_resolved() -> None:
    findings = [
        _f(id="f_a", severity="high", file="a.py", start_line=1, end_line=1),
        _f(id="f_b", severity="low", file="b.py"),
        _f(id="f_c", severity="critical", file="c.py", start_line=2, end_line=2),
        _f(id="f_d", severity="high", file="d.py", status="resolved"),
    ]
    surfaced = filter_findings_for_publish(findings, severity_threshold="medium", cap=10)
    assert [f["id"] for f in surfaced] == ["f_c", "f_a"]


def test_filter_findings_for_publish_is_uncapped_by_default() -> None:
    findings = [_f(id=f"f_{i}", severity="high", file=f"f{i}.py") for i in range(20)]
    surfaced = filter_findings_for_publish(findings, severity_threshold="medium")
    assert len(surfaced) == 20
    assert len(filter_findings_for_publish(findings, cap=5)) == 5


def test_filter_findings_for_publish_orders_by_rank_before_severity() -> None:
    findings = [
        _f(id="f_unranked", severity="critical", file="a.py"),
        _f(id="f_second", severity="critical", file="b.py", rank=2),
        _f(id="f_first", severity="medium", file="c.py", rank=1),
    ]
    surfaced = filter_findings_for_publish(findings, severity_threshold="medium")
    assert [f["id"] for f in surfaced] == ["f_first", "f_second", "f_unranked"]


@pytest.mark.usefixtures("registry_db")
async def test_first_access_backfills_metadata_findings_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _metadata_client({"findings": [_f(id="f_a"), _f(id="f_b", description="other")]})

    with (
        patch("agent.review.findings.get_client", return_value=client),
        caplog.at_level(logging.INFO, logger="agent.review.findings"),
    ):
        first = await list_findings("tid")
        client.threads.get.return_value = {"metadata": {"findings": []}}
        second = await list_findings("tid")

    assert [f["id"] for f in first] == ["f_a", "f_b"]
    assert second == first
    client.threads.get.assert_awaited_once()
    backfills = [r for r in caplog.records if r.message.startswith("Backfilled reviewer findings")]
    assert len(backfills) == 1
    assert backfills[0].__dict__["reviewer_thread_id"] == "tid"
    assert backfills[0].__dict__["pr_repo_full_name"] == "acme/app"
    assert backfills[0].__dict__["pr_number"] == 1
    assert backfills[0].__dict__["finding_count"] == 2


@pytest.mark.usefixtures("registry_db")
async def test_findings_are_stored_under_the_threads_pull_request() -> None:
    from agent.github.pull_requests import PullRequest

    with patch("agent.review.findings.get_client", return_value=_metadata_client(number=7)):
        await append_finding("tid", _f(id="f_a"))

    pull_request = await PullRequest.get("acme", "app", 7)
    assert pull_request is not None
    async with postgres.session() as session:
        stored = await session.scalars(
            select(FindingRow.id).where(FindingRow.pull_request_id == pull_request.id)
        )
        assert list(stored) == ["f_a"]


@pytest.mark.usefixtures("registry_db")
async def test_a_thread_without_pull_request_metadata_stores_nothing() -> None:
    client = AsyncMock()
    client.threads.get.return_value = {"metadata": {"findings": [_f(id="f_a")]}}

    with patch("agent.review.findings.get_client", return_value=client):
        with pytest.raises(ReviewerThreadUnlinkedError):
            await append_finding("tid", _f(id="f_b"))


@pytest.mark.usefixtures("registry_db")
async def test_reply_authors_link_to_registered_users_by_login() -> None:
    from agent.users.models import User

    user = await User.sign_in("github", "1001", login="trusted-user")
    finding = _f(id="f_a")
    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        await append_finding("tid", finding)
        for comment_id, author in ((1, "Trusted-User"), (2, "stranger")):
            await append_finding_interaction(
                "tid",
                "f_a",
                {
                    "kind": "human_reply",
                    "github_comment_id": comment_id,
                    "author": author,
                    "body": "hm",
                },
            )
        (persisted,) = await list_findings("tid")

    assert [i["author"] for i in persisted["interactions"]] == ["Trusted-User", "stranger"]
    async with postgres.session() as session:
        linked = await session.execute(
            select(InteractionRow.author, InteractionRow.author_user_id).order_by(
                InteractionRow.position
            )
        )
        assert list(linked.tuples()) == [("Trusted-User", user.id), ("stranger", None)]


@pytest.mark.usefixtures("registry_db")
async def test_list_findings_returns_empty_on_missing_metadata() -> None:
    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        assert await list_findings("tid") == []


@pytest.mark.usefixtures("registry_db")
async def test_list_findings_coerces_bad_entries() -> None:
    client = _metadata_client(
        {
            "findings": [
                {"id": "f_ok", "severity": "high", "file": "x.py"},
                {"missing_id": True},
                "not-a-dict",
            ]
        }
    )
    with patch("agent.review.findings.get_client", return_value=client):
        findings = await list_findings("tid")
    assert [f["id"] for f in findings] == ["f_ok"]


@pytest.mark.usefixtures("registry_db")
async def test_failed_backfill_read_copies_nothing_and_retries_later() -> None:
    client = _metadata_client()
    client.threads.get.side_effect = RuntimeError("transient")

    with patch("agent.review.findings.get_client", return_value=client):
        with pytest.raises(RuntimeError, match="transient"):
            await mutate_findings("tid", lambda findings: bool(findings.append(_f(id="f_new"))))
        assert await list_findings("tid") == []

        client.threads.get.side_effect = None
        client.threads.get.return_value = _metadata_client(
            {"findings": [_f(id="f_legacy")]}
        ).threads.get.return_value
        findings = await list_findings("tid")

    assert [f["id"] for f in findings] == ["f_legacy"]


@pytest.mark.usefixtures("registry_db")
async def test_first_access_to_a_missing_thread_raises_domain_error() -> None:
    client = _metadata_client()
    client.threads.get.side_effect = _not_found()

    with patch("agent.review.findings.get_client", return_value=client):
        with pytest.raises(ReviewerThreadMissingError) as excinfo:
            await replace_findings("tid", [_f(id="f_a")])

    assert excinfo.value.thread_id == "tid"


async def _read_persisted(record: dict[str, Any]) -> Finding:
    with patch(
        "agent.review.findings.get_client", return_value=_metadata_client({"findings": [record]})
    ):
        findings = await list_findings("tid")
    return findings[0]


@pytest.mark.usefixtures("registry_db")
async def test_reading_legacy_singulars_folds_them_into_the_canonical_lists() -> None:
    finding = await _read_persisted(
        {
            "id": "f_legacy",
            "github_review_comment_id": 11,
            "github_review_thread_id": "THREAD_1",
            "github_review_comment_ids": [12],
            "github_review_thread_ids": ["THREAD_2"],
        }
    )

    assert comment_ids_for_finding(finding) == [11, 12]
    assert thread_ids_for_finding(finding) == ["THREAD_1", "THREAD_2"]
    assert "github_review_comment_id" not in finding
    assert "github_review_thread_id" not in finding
    assert surface_state_of(finding) == "surfaced"


@pytest.mark.usefixtures("registry_db")
async def test_reading_legacy_resolved_flag_becomes_resolved_surface_state() -> None:
    finding = await _read_persisted(
        {
            "id": "f_legacy",
            "github_review_thread_ids": ["THREAD_1"],
            "github_thread_resolved": True,
        }
    )

    assert is_thread_resolved(finding) is True
    assert is_surfaced(finding) is True
    assert "github_thread_resolved" not in finding


@pytest.mark.usefixtures("registry_db")
async def test_reading_legacy_surface_record_folds_ids_and_state() -> None:
    finding = await _read_persisted(
        {
            "id": "f_legacy",
            "anchor": {"file": "a.py", "start_line": 1, "end_line": 1, "side": "RIGHT"},
            "surface": {
                "finding_id": "f_legacy",
                "state": "resolve_pending",
                "github_review_id": 900,
                "github_review_comment_id": 11,
                "github_review_thread_id": "THREAD_1",
                "severity_threshold_at_publish": "high",
            },
        }
    )

    assert comment_ids_for_finding(finding) == [11]
    assert thread_ids_for_finding(finding) == ["THREAD_1"]
    assert review_id_for_finding(finding) == 900
    assert surface_state_of(finding) == "resolve_pending"
    assert "surface" not in finding
    assert "anchor" not in finding


@pytest.mark.usefixtures("registry_db")
async def test_reading_a_never_published_legacy_record_stays_not_surfaced() -> None:
    finding = await _read_persisted(
        {
            "id": "f_legacy",
            "github_review_comment_id": None,
            "github_review_thread_id": None,
            "github_thread_resolved": False,
            "surface": {"finding_id": "f_legacy", "state": "not_surfaced"},
        }
    )

    assert comment_ids_for_finding(finding) == []
    assert thread_ids_for_finding(finding) == []
    assert review_id_for_finding(finding) is None
    assert is_surfaced(finding) is False


@pytest.mark.usefixtures("registry_db")
async def test_a_sparse_legacy_record_is_stored_complete() -> None:
    client = _metadata_client(
        {
            "findings": [
                {
                    "id": "f_legacy",
                    "status": "open",
                    "github_review_comment_id": 11,
                    "github_thread_resolved": True,
                    "surface": {"finding_id": "f_legacy", "state": "surfaced"},
                }
            ]
        }
    )

    with patch("agent.review.findings.get_client", return_value=client):
        await update_finding_fields("tid", "f_legacy", {"status": "resolved"})
        (persisted,) = await list_findings("tid")

    assert persisted["status"] == "resolved"
    assert persisted["github_review_comment_ids"] == [11]
    assert persisted["surface_state"] == "resolved"
    assert persisted["severity"] == "low"
    assert persisted["rank"] is None
    assert persisted["interactions"] == []


@pytest.mark.usefixtures("registry_db")
async def test_append_finding_appends_to_existing_list() -> None:
    client = _metadata_client({"findings": [_f(id="f_a")]})

    with patch("agent.review.findings.get_client", return_value=client):
        result = await append_finding("tid", _f(id="f_b", description="different"))
        persisted = await list_findings("tid")

    assert result["finding"]["id"] == "f_b"
    assert result["created"] is True
    assert [f["id"] for f in persisted] == ["f_a", "f_b"]


@pytest.mark.usefixtures("registry_db")
async def test_concurrent_append_finding_preserves_distinct_findings() -> None:
    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        first, second = await asyncio.gather(
            append_finding("tid", _f(id="f_a", description="first")),
            append_finding("tid", _f(id="f_b", description="second")),
        )
        persisted = await list_findings("tid")

    assert first["created"] is True
    assert second["created"] is True
    assert {finding["id"] for finding in persisted} == {"f_a", "f_b"}


@pytest.mark.usefixtures("registry_db")
async def test_concurrent_identical_findings_are_idempotent() -> None:
    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        first, second = await asyncio.gather(
            append_finding("tid", _f(id="f_a")),
            append_finding("tid", _f(id="f_b")),
        )
        persisted = await list_findings("tid")

    assert sum(result["created"] for result in (first, second)) == 1
    assert first["finding"]["id"] == second["finding"]["id"]
    assert len(persisted) == 1


@pytest.mark.usefixtures("registry_db")
async def test_mutate_findings_reads_latest_before_mutating() -> None:
    seen: list[str] = []

    def _mutator(findings: list[Finding]) -> bool:
        seen.extend(f["id"] for f in findings)
        findings[0]["status"] = "resolved"
        return True

    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        await append_finding("tid", _f(id="f_fresh"))
        result = await mutate_findings("tid", _mutator)
        persisted = await list_findings("tid")

    assert seen == ["f_fresh"]
    assert result[0]["status"] == "resolved"
    assert persisted[0]["status"] == "resolved"


@pytest.mark.usefixtures("registry_db")
async def test_update_finding_fields_mutates_only_target() -> None:
    client = _metadata_client(
        {"findings": [_f(id="f_a", description="orig-a"), _f(id="f_b", description="orig-b")]}
    )

    with patch("agent.review.findings.get_client", return_value=client):
        updated = await update_finding_fields("tid", "f_b", {"status": "resolved"})
        persisted = await list_findings("tid")

    assert updated is not None
    assert updated["status"] == "resolved"
    by_id = {f["id"]: f for f in persisted}
    assert by_id["f_a"]["status"] == "open"
    assert by_id["f_b"]["status"] == "resolved"


@pytest.mark.usefixtures("registry_db")
async def test_update_finding_fields_returns_none_for_unknown_id() -> None:
    with patch(
        "agent.review.findings.get_client",
        return_value=_metadata_client({"findings": [_f(id="f_a")]}),
    ):
        result = await update_finding_fields("tid", "f_missing", {"status": "resolved"})
        persisted = await list_findings("tid")
    assert result is None
    assert persisted[0]["status"] == "open"


@pytest.mark.usefixtures("registry_db")
async def test_replace_findings_keeps_records_added_since_the_snapshot() -> None:
    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        await append_finding("tid", _f(id="f_a", description="a"))
        snapshot = await list_findings("tid")
        await append_finding("tid", _f(id="f_b", description="b"))
        snapshot[0]["status"] = "resolved"
        await replace_findings("tid", snapshot)
        persisted = await list_findings("tid")

    assert [(f["id"], f["status"]) for f in persisted] == [("f_a", "resolved"), ("f_b", "open")]


@pytest.mark.usefixtures("registry_db")
async def test_findings_by_thread_reads_unmigrated_threads_from_metadata_without_copying() -> None:
    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        await append_finding("stored", _f(id="f_stored"))

    unmigrated = {"findings": [_f(id="f_meta")]}
    result = await findings_by_thread({"stored": {"findings": []}, "legacy": unmigrated})

    assert [f["id"] for f in result["stored"]] == ["f_stored"]
    assert [f["id"] for f in result["legacy"]] == ["f_meta"]
    client = _metadata_client({"findings": [_f(id="f_later")]}, number=2)
    with patch("agent.review.findings.get_client", return_value=client):
        assert [f["id"] for f in await list_findings("legacy")] == ["f_later"]


async def test_set_reviewer_thread_metadata_includes_kind() -> None:
    fake_client = AsyncMock()
    with patch("agent.review.findings.get_client", return_value=fake_client):
        await set_reviewer_thread_metadata("tid", watch=True, last_reviewed_sha="sha")
    metadata = fake_client.threads.update.await_args.kwargs["metadata"]
    assert metadata["kind"] == "reviewer"
    assert metadata["watch"] is True
    assert metadata["last_reviewed_sha"] == "sha"
    assert "pr" not in metadata
    assert "findings" not in metadata


async def test_set_reviewer_thread_metadata_persists_head_sha() -> None:
    fake_client = AsyncMock()
    with patch("agent.review.findings.get_client", return_value=fake_client):
        await set_reviewer_thread_metadata("tid", head_sha="newhead")
    metadata = fake_client.threads.update.await_args.kwargs["metadata"]
    assert metadata["head_sha"] == "newhead"


async def test_resolve_review_head_sha_prefers_metadata_over_config() -> None:
    """A mid-run push records the live head in thread metadata; it must win over
    the stale head frozen in the run's config."""
    fake_client = _metadata_client({"head_sha": "metahead"})
    with patch("agent.review.findings.get_client", return_value=fake_client):
        head = await resolve_review_head_sha("tid", RunConfig(head_sha="confighead"))
    assert head == "metahead"


async def test_resolve_review_head_sha_falls_back_to_config_when_metadata_empty() -> None:
    with patch("agent.review.findings.get_client", return_value=_metadata_client()):
        head = await resolve_review_head_sha("tid", RunConfig(head_sha="confighead"))
    assert head == "confighead"


async def test_resolve_review_head_sha_falls_back_without_thread_id() -> None:
    fake_client = AsyncMock()
    with patch("agent.review.findings.get_client", return_value=fake_client):
        head = await resolve_review_head_sha("", RunConfig(head_sha="confighead"))
    assert head == "confighead"
    fake_client.threads.get.assert_not_called()


async def test_get_thread_metadata_raises_domain_error_when_thread_missing() -> None:
    """A missing thread must surface as ReviewerThreadMissingError, not be
    swallowed into ``{}`` — that produced misleading tool results like
    "No finding found" instead of the do-not-retry contract."""
    from agent.review.findings import get_thread_metadata

    fake_client = AsyncMock()
    fake_client.threads.get.side_effect = _not_found()

    with patch("agent.review.findings.get_client", return_value=fake_client):
        with pytest.raises(ReviewerThreadMissingError):
            await get_thread_metadata("tid")


async def test_get_thread_metadata_still_degrades_on_other_failures() -> None:
    from agent.review.findings import get_thread_metadata

    fake_client = AsyncMock()
    fake_client.threads.get.side_effect = RuntimeError("transient")

    with patch("agent.review.findings.get_client", return_value=fake_client):
        assert await get_thread_metadata("tid") == {}


async def test_set_reviewer_thread_metadata_raises_domain_error_when_thread_missing() -> None:
    fake_client = AsyncMock()
    fake_client.threads.update.side_effect = _not_found("PATCH")

    with patch("agent.review.findings.get_client", return_value=fake_client):
        with pytest.raises(ReviewerThreadMissingError):
            await set_reviewer_thread_metadata("tid", last_reviewed_sha="sha")
