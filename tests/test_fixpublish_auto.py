import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path("/home/tom/ai/open-swe")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _make_fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "fixpublish_auto.sh", repo / "scripts" / "fixpublish_auto.sh")
    return repo


def _make_fake_python(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "python",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-" ]]; then
  exec python3 "$@"
fi
if [[ "$*" == *"--analyze-validation-failure"* ]]; then
  cat <<'EOF'
{"validation_error_type":"syntax","failing_command":"python -m py_compile local_fix_agent.py","failing_test_files":["tests/test_local_fix_agent_publish.py"],"failing_source_files":["local_fix_agent.py"],"repair_targets":["local_fix_agent.py"],"repair_goal":"Fix the syntax error blocking validation in local_fix_agent.py.","repair_context_used":true,"failure_context_snippet":"File \\"local_fix_agent.py\\", line 12\\nSyntaxError: invalid syntax"}
EOF
  exit 0
fi
printf '%s\\n' "$*" >>"$FIXPUBLISH_AUTO_PYTHON_LOG"
exit 0
""",
    )


def _make_fake_fixpublish(repo: Path) -> None:
    _write_executable(
        repo / "scripts" / "fixpublish.sh",
        """#!/usr/bin/env bash
set -euo pipefail
count_file="$FIXPUBLISH_AUTO_COUNT_FILE"
count=0
if [[ -f "$count_file" ]]; then
  count="$(cat "$count_file")"
fi
count=$((count + 1))
printf '%s' "$count" >"$count_file"
output_var="FIXPUBLISH_AUTO_RUN_${count}_OUTPUT"
status_var="FIXPUBLISH_AUTO_RUN_${count}_STATUS"
printf '%s' "${!output_var-}"
exit "${!status_var:-0}"
""",
    )


def test_fixpublish_auto_success_without_retry(tmp_path: Path) -> None:
    repo = _make_fake_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_python(bin_dir)
    _make_fake_fixpublish(repo)
    count_file = tmp_path / "count.txt"
    python_log = tmp_path / "python.log"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FIXPUBLISH_AUTO_COUNT_FILE"] = str(count_file)
    env["FIXPUBLISH_AUTO_PYTHON_LOG"] = str(python_log)
    env["FIXPUBLISH_AUTO_RUN_1_OUTPUT"] = (
        "FINAL: validation succeeded, publish succeeded\n"
        "pr_url: https://github.com/example/repo/pull/1\n"
    )
    env["FIXPUBLISH_AUTO_RUN_1_STATUS"] = "0"

    result = subprocess.run(
        ["./scripts/fixpublish_auto.sh"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "FINAL: validation succeeded, publish succeeded" in result.stdout
    assert "A PR was created/reused at https://github.com/example/repo/pull/1" in result.stdout
    assert count_file.read_text() == "1"
    assert not python_log.exists()


def test_fixpublish_auto_retries_once_after_validation_block(tmp_path: Path) -> None:
    repo = _make_fake_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_python(bin_dir)
    _make_fake_fixpublish(repo)
    count_file = tmp_path / "count.txt"
    python_log = tmp_path / "python.log"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FIXPUBLISH_AUTO_COUNT_FILE"] = str(count_file)
    env["FIXPUBLISH_AUTO_PYTHON_LOG"] = str(python_log)
    env["FIXPUBLISH_AUTO_RUN_1_OUTPUT"] = (
        "=== VALIDATION RECORD ===\n"
        "validation_result: failed\n"
        "validation_record_reason: pytest -q failed\n"
        "FINAL: validation failed\n"
    )
    env["FIXPUBLISH_AUTO_RUN_1_STATUS"] = "1"
    env["FIXPUBLISH_AUTO_RUN_2_OUTPUT"] = (
        "FINAL: validation succeeded, publish succeeded\n"
        "pr_url: https://github.com/example/repo/pull/2\n"
    )
    env["FIXPUBLISH_AUTO_RUN_2_STATUS"] = "0"

    result = subprocess.run(
        ["./scripts/fixpublish_auto.sh"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Validation failed; attempting repair before one final publish retry." in result.stdout
    assert "validation_error_type: syntax" in result.stdout
    assert "repair_targets: ['local_fix_agent.py']" in result.stdout
    assert "repair_context_used: true" in result.stdout
    assert result.stdout.count("FINAL: validation succeeded, publish succeeded") == 1
    assert "A PR was created/reused at https://github.com/example/repo/pull/2" in result.stdout
    assert count_file.read_text() == "2"
    repair_log = python_log.read_text().strip()
    assert f"local_fix_agent.py --repo {repo} --mode quick --max-steps 2 --no-upstream-sync --repair-context-file " in repair_log
    assert " --test-cmd python -m py_compile local_fix_agent.py" in repair_log


def test_fixpublish_auto_does_not_retry_non_validation_block(tmp_path: Path) -> None:
    repo = _make_fake_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_python(bin_dir)
    _make_fake_fixpublish(repo)
    count_file = tmp_path / "count.txt"
    python_log = tmp_path / "python.log"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FIXPUBLISH_AUTO_COUNT_FILE"] = str(count_file)
    env["FIXPUBLISH_AUTO_PYTHON_LOG"] = str(python_log)
    env["FIXPUBLISH_AUTO_RUN_1_OUTPUT"] = (
        "FINAL: validation succeeded, publish blocked\n"
        "reason: Publish blocked because git remote 'origin' is not configured.\n"
    )
    env["FIXPUBLISH_AUTO_RUN_1_STATUS"] = "1"

    result = subprocess.run(
        ["./scripts/fixpublish_auto.sh"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Validation failed; attempting repair before one final publish retry." not in result.stdout
    assert "Publish blocked because git remote 'origin' is not configured." in result.stdout
    assert count_file.read_text() == "1"
    assert not python_log.exists()
