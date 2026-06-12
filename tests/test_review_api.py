from agent.dashboard.review_api import (
    _finding_counts,
    _serialize_finding,
    _thread_review_summary,
    classify_finding,
    reviewer_thread_id,
)
from agent.webapp import generate_reviewer_thread_id


def test_classify_finding():
    assert classify_finding({"severity": "critical", "confidence": "high"}) == "bug"
    assert classify_finding({"severity": "high", "confidence": "high"}) == "bug"
    assert classify_finding({"severity": "high", "confidence": "medium"}) == "investigate"
    assert classify_finding({"severity": "medium", "confidence": "high"}) == "investigate"
    assert classify_finding({"severity": "low", "confidence": "high"}) == "informational"


def test_finding_counts_only_open_in_groups():
    findings = [
        {"id": "f_1", "severity": "high", "confidence": "high", "status": "open"},
        {"id": "f_2", "severity": "medium", "confidence": "high", "status": "open"},
        {"id": "f_3", "severity": "high", "confidence": "high", "status": "resolved"},
        {"id": "f_4", "severity": "low", "confidence": "low", "status": "dismissed"},
    ]
    counts = _finding_counts(findings)
    assert counts == {"open": 2, "resolved": 1, "dismissed": 1, "bugs": 1, "flags": 1}


def test_serialize_finding_outdated():
    finding = {"id": "f_1", "last_confirmed_sha": "aaa"}
    assert _serialize_finding(finding, "bbb")["outdated"] is True
    assert _serialize_finding(finding, "aaa")["outdated"] is False
    assert _serialize_finding(finding, None)["outdated"] is False
    assert _serialize_finding({"id": "f_2"}, "bbb")["outdated"] is False


def test_thread_review_summary():
    thread = {
        "thread_id": "t1",
        "status": "idle",
        "updated_at": "2026-06-10T00:00:00Z",
        "metadata": {
            "kind": "reviewer",
            "pr": {
                "owner": "acme",
                "name": "repo",
                "number": 7,
                "title": "Fix things",
                "head_ref": "fix",
                "base_ref": "main",
            },
            "head_sha": "abc",
            "watch": True,
            "latest_run_status": "success",
            "findings": [{"id": "f_1", "severity": "high", "confidence": "high", "status": "open"}],
        },
    }
    summary = _thread_review_summary(thread)
    assert summary is not None
    assert summary["owner"] == "acme"
    assert summary["number"] == 7
    assert summary["status"] == "idle"
    assert summary["watch"] is True
    assert summary["counts"]["bugs"] == 1


def test_thread_review_summary_requires_pr_meta():
    assert _thread_review_summary({"metadata": {"kind": "reviewer"}}) is None


def test_reviewer_thread_id_matches_webapp():
    assert reviewer_thread_id("acme", "repo", 7) == generate_reviewer_thread_id("acme", "repo", 7)
