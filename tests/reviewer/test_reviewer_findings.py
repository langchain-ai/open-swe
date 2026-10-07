"""Unit tests for the Finding schema and its PostgreSQL-backed storage."""

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openswe.review.findings import (
    Finding,
    ReviewerThreadMissingError,
    ReviewerThreadUnlinkedError,
    append_finding,
    comment_ids_for_finding,
    filter_findings_for_publish,
    findings_by_thread,
    list_findings,
    mutate_findings,
    new_finding,
    replace_findings,
    review_id_for_finding,
    surface_state_of,
    thread_ids_for_finding,
)


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
        patch("openswe.review.findings.get_client", return_value=client),
        caplog.at_level(logging.INFO, logger="openswe.review.findings"),
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
async def test_a_thread_without_pull_request_metadata_stores_nothing() -> None:
    client = AsyncMock()
    client.threads.get.return_value = {"metadata": {"findings": [_f(id="f_a")]}}

    with patch("openswe.review.findings.get_client", return_value=client):
        with pytest.raises(ReviewerThreadUnlinkedError):
            await append_finding("tid", _f(id="f_b"))


@pytest.mark.usefixtures("registry_db")
async def test_failed_backfill_read_copies_nothing_and_retries_later() -> None:
    client = _metadata_client()
    client.threads.get.side_effect = RuntimeError("transient")

    with patch("openswe.review.findings.get_client", return_value=client):
        with pytest.raises(RuntimeError, match="transient"):
            await mutate_findings("tid", lambda findings: bool(findings.append(_f(id="f_new"))))
        assert await list_findings("tid") == []

        client.threads.get.side_effect = None
        client.threads.get.return_value = _metadata_client(
            {"findings": [_f(id="f_legacy")]}
        ).threads.get.return_value
        findings = await list_findings("tid")

    assert [f["id"] for f in findings] == ["f_legacy"]


async def _read_persisted(record: dict[str, Any]) -> Finding:
    with patch(
        "openswe.review.findings.get_client", return_value=_metadata_client({"findings": [record]})
    ):
        findings = await list_findings("tid")
    return findings[0]


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
async def test_concurrent_append_finding_preserves_distinct_findings() -> None:
    with patch("openswe.review.findings.get_client", return_value=_metadata_client()):
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
    with patch("openswe.review.findings.get_client", return_value=_metadata_client()):
        first, second = await asyncio.gather(
            append_finding("tid", _f(id="f_a")),
            append_finding("tid", _f(id="f_b")),
        )
        persisted = await list_findings("tid")

    assert sum(result["created"] for result in (first, second)) == 1
    assert first["finding"]["id"] == second["finding"]["id"]
    assert len(persisted) == 1


@pytest.mark.usefixtures("registry_db")
async def test_replace_findings_keeps_records_added_since_the_snapshot() -> None:
    with patch("openswe.review.findings.get_client", return_value=_metadata_client()):
        await append_finding("tid", _f(id="f_a", description="a"))
        snapshot = await list_findings("tid")
        await append_finding("tid", _f(id="f_b", description="b"))
        snapshot[0]["status"] = "resolved"
        await replace_findings("tid", snapshot)
        persisted = await list_findings("tid")

    assert [(f["id"], f["status"]) for f in persisted] == [("f_a", "resolved"), ("f_b", "open")]


@pytest.mark.usefixtures("registry_db")
async def test_findings_by_thread_reads_metadata_when_a_copy_fails() -> None:
    unlinked = {"findings": [_f(id="f_meta")]}

    result = await findings_by_thread({"unlinked": unlinked})

    assert [f["id"] for f in result["unlinked"]] == ["f_meta"]


async def test_get_thread_metadata_raises_domain_error_when_thread_missing() -> None:
    """A missing thread must surface as ReviewerThreadMissingError, not be
    swallowed into ``{}`` — that produced misleading tool results like
    "No finding found" instead of the do-not-retry contract."""
    from openswe.review.findings import get_thread_metadata

    fake_client = AsyncMock()
    fake_client.threads.get.side_effect = _not_found()

    with patch("openswe.review.findings.get_client", return_value=fake_client):
        with pytest.raises(ReviewerThreadMissingError):
            await get_thread_metadata("tid")
