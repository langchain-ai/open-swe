from __future__ import annotations

from pathlib import Path

import local_fix_agent as lfa


def test_new_wrapper_script_requires_docs(monkeypatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "operator_diff_text", lambda current_repo, changed_paths: "diff --git a/scripts/newwrap.sh b/scripts/newwrap.sh")
    monkeypatch.setattr(lfa, "detect_stale_doc_signals", lambda current_repo: [])
    monkeypatch.setattr(lfa, "load_docs_state", lambda current_repo: {"last_refresh_mode": "rewrite"})

    docs_plan = lfa.assess_docs_impact(repo, ["scripts/newwrap.sh"])

    assert docs_plan["docs_required"] is True
    assert "wrappers" in docs_plan["docs_categories"]


def test_new_cli_help_text_requires_docs(monkeypatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "operator_diff_text",
        lambda current_repo, changed_paths: 'parser.add_argument("--new-flag", help="new operator option")',
    )
    monkeypatch.setattr(lfa, "detect_stale_doc_signals", lambda current_repo: [])
    monkeypatch.setattr(lfa, "load_docs_state", lambda current_repo: {"last_refresh_mode": "rewrite"})

    docs_plan = lfa.assess_docs_impact(repo, ["local_fix_agent.py"])

    assert docs_plan["docs_required"] is True
    assert "cli" in docs_plan["docs_categories"]


def test_multiple_operator_visible_changes_choose_rewrite(monkeypatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "operator_diff_text",
        lambda current_repo, changed_paths: "\n".join(
            [
                'parser.add_argument("--publish-thing", help="publish")',
                'print("\\n=== PUBLISH SUMMARY ===")',
                'result["control_path"] = "blocked_auth"',
            ]
        ),
    )
    monkeypatch.setattr(lfa, "detect_stale_doc_signals", lambda current_repo: [])
    monkeypatch.setattr(lfa, "load_docs_state", lambda current_repo: {"last_refresh_mode": "patch"})

    docs_plan = lfa.assess_docs_impact(repo, ["local_fix_agent.py"])

    assert docs_plan["docs_required"] is True
    assert docs_plan["docs_refresh_mode"] == "rewrite"


def test_localized_change_can_choose_patch(monkeypatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "operator_diff_text",
        lambda current_repo, changed_paths: 'parser.add_argument("--new-flag", help="small docs change")',
    )
    monkeypatch.setattr(lfa, "detect_stale_doc_signals", lambda current_repo: [])
    monkeypatch.setattr(lfa, "load_docs_state", lambda current_repo: {"last_refresh_mode": "rewrite"})

    docs_plan = lfa.assess_docs_impact(repo, ["local_fix_agent.py"])

    assert docs_plan["docs_required"] is True
    assert docs_plan["docs_refresh_mode"] == "patch"


def test_pure_internal_refactor_does_not_require_docs(monkeypatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "operator_diff_text",
        lambda current_repo, changed_paths: "internal helper refactor without operator surface changes",
    )
    monkeypatch.setattr(lfa, "detect_stale_doc_signals", lambda current_repo: [])
    monkeypatch.setattr(lfa, "load_docs_state", lambda current_repo: {"last_refresh_mode": "rewrite"})

    docs_plan = lfa.assess_docs_impact(repo, ["local_fix_agent.py"])

    assert docs_plan["docs_required"] is False
    assert docs_plan["docs_refresh_mode"] == "none"
