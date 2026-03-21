from __future__ import annotations

from pathlib import Path

import pytest

import local_fix_agent as lfa


def test_classify_config_file_detects_nginx_from_content(tmp_path: Path) -> None:
    config_path = tmp_path / "site.conf"
    config_path.write_text("server {\n    listen 80;\n    location / { proxy_pass http://127.0.0.1:8080; }\n}\n")

    detected = lfa.classify_config_file(config_path, config_path.read_text())

    assert detected["config_type"] == "nginx"
    assert detected["classification_source"] == "content_pattern"


def test_run_config_workflow_cleanup_keeps_changes_when_validation_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "nginx.conf"
    config_path.write_text("server {\n    listen 80;   \n\n\n}\n")
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (0, "syntax ok"))

    result = lfa.run_config_workflow(
        repo,
        config_path=config_path,
        task="cleanup",
        config_type="nginx",
        validation_command="nginx -t -c nginx.conf",
    )

    assert result["ok"] is True
    assert result["validation_result"] == "success"
    assert result["changes_made"] is True
    assert config_path.read_text() == "server {\n    listen 80;\n\n}\n"


def test_run_config_workflow_reverts_changes_when_validation_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "php.ini"
    original = "memory_limit=256M   \n"
    config_path.write_text(original)
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (1, "validation failed"))

    result = lfa.run_config_workflow(
        repo,
        config_path=config_path,
        task="cleanup",
        config_type="php_ini",
        validation_command="php -n -c php.ini -m",
    )

    assert result["ok"] is False
    assert result["validation_result"] == "blocked"
    assert result["changes_made"] is False
    assert "validation failed" in result["blocked_reason"]
    assert config_path.read_text() == original


def test_run_config_workflow_compare_reports_diff_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    left = repo / "left.conf"
    right = repo / "right.conf"
    left.write_text("server {\n    listen 80;\n}\n")
    right.write_text("server {\n    listen 8080;\n}\n")
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (0, "syntax ok"))

    result = lfa.run_config_workflow(
        repo,
        config_path=left,
        task="compare",
        config_type="nginx",
        compare_path=str(right),
        validation_command="",
    )

    assert result["ok"] is True
    assert result["validation_result"] == "skipped"
    assert result["compare_summary"] == "2 line(s) differ"
    assert result["changes_made"] is False

