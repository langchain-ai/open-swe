from agent.dashboard.review_api import (
    _finding_counts,
    _serialize_finding,
    _thread_review_summary,
    classify_finding,
    parse_diff_files,
    reviewer_thread_id,
)
from agent.webapp import generate_reviewer_thread_id

DIFF = """\
diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@
 import os
-x = 1
+x = 2
+y = 3
 print(x)
diff --git a/new.txt b/new.txt
new file mode 100644
index 000..333
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
index 444..000
--- a/gone.txt
+++ /dev/null
@@ -1,1 +0,0 @@
-bye
"""


def test_parse_diff_files_statuses_and_counts():
    files = parse_diff_files(DIFF)
    by_path = {f["path"]: f for f in files}
    assert by_path["src/app.py"]["status"] == "modified"
    assert by_path["src/app.py"]["additions"] == 2
    assert by_path["src/app.py"]["deletions"] == 1
    assert by_path["new.txt"]["status"] == "added"
    assert by_path["new.txt"]["additions"] == 2
    assert by_path["gone.txt"]["status"] == "deleted"
    assert by_path["gone.txt"]["deletions"] == 1


def test_parse_diff_files_line_numbers():
    files = parse_diff_files(DIFF)
    app = next(f for f in files if f["path"] == "src/app.py")
    lines = app["hunks"][0]["lines"]
    assert lines[0] == {"kind": "context", "old_line": 1, "new_line": 1, "text": "import os"}
    assert lines[1] == {"kind": "del", "old_line": 2, "text": "x = 1"}
    assert lines[2] == {"kind": "add", "new_line": 2, "text": "x = 2"}
    assert lines[3] == {"kind": "add", "new_line": 3, "text": "y = 3"}
    assert lines[4] == {"kind": "context", "old_line": 3, "new_line": 4, "text": "print(x)"}


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
