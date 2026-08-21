"""Turn checkpoints: git plumbing output parsing and checkpoint bookkeeping."""

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.sandboxes.turn_checkpoint import (
    _diff_command,
    build_diff_files,
    mark_checkpoint_plan_mode,
    merge_checkpoint,
    parse_numstat,
    read_turn_diff,
    record_turn_checkpoint,
)


def test_build_diff_files_maps_numstat_status_and_contents() -> None:
    numstat = "\0".join(["1\t2\tsrc/a.py", "3\t0\tsrc/new.py", "-\t-\tlogo.png", ""])
    name_status = "\0".join(["M", "src/a.py", "A", "src/new.py", "M", "logo.png", ""])
    contents = {
        "src/a.py": {
            "base": base64.b64encode(b"old\n").decode(),
            "head": base64.b64encode(b"new\n").decode(),
        },
        "src/new.py": {"base": None, "head": base64.b64encode(b"hi\n").decode()},
        "logo.png": {"base": False, "head": False},
    }

    files = build_diff_files(numstat, name_status, contents)

    assert [(f["path"], f["status"], f["additions"], f["deletions"]) for f in files] == [
        ("src/a.py", "modified", 1, 2),
        ("src/new.py", "added", 3, 0),
        ("logo.png", "modified", 0, 0),
    ]
    assert files[0]["originalContent"] == "old\n"
    assert files[1]["originalContent"] is None
    assert files[2]["unrenderable"] is True


def test_diff_command_bounds_file_metadata_and_keeps_full_summary(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "binary.bin").write_bytes(b"\x00\x01")
    for index in range(12):
        (tmp_path / f"change-{index:02}.txt").write_text(f"change {index}\n")

    result = subprocess.run(
        _diff_command(str(tmp_path), "HEAD", None, 10),
        cwd=tmp_path,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    stats = parse_numstat(payload["numstat"])

    assert len(stats) == 10
    assert any(additions is None and deletions is None for _, additions, deletions in stats)
    assert payload["summary"] == {"files": 13, "additions": 12, "deletions": 0}
    assert "change-09.txt" not in payload["numstat"]


class _FakeSandbox:
    def __init__(self, *payloads: dict[str, Any]) -> None:
        self.payloads = list(payloads)
        self.commands: list[str] = []

    async def aexecute(self, command: str, *, timeout: int) -> dict[str, Any]:
        self.commands.append(command)
        if not self.payloads:
            raise AssertionError("unexpected sandbox command")
        return {"output": json.dumps(self.payloads.pop(0)), "exit_code": 0}


async def test_read_turn_diff_metadata_only_skips_file_contents() -> None:
    records = [f"1\t0\tchange-{index:02}.txt" for index in range(9)]
    records.append("-\t-\tbinary.bin")
    statuses = [part for index in range(9) for part in ("A", f"change-{index:02}.txt")]
    statuses.extend(("A", "binary.bin"))
    sandbox = _FakeSandbox(
        {
            "head": "head-tree",
            "numstat": "\0".join([*records, ""]),
            "nameStatus": "\0".join([*statuses, ""]),
            "summary": {"files": 15, "additions": 14, "deletions": 2},
        }
    )

    result = await read_turn_diff(
        sandbox,
        None,
        "base-ref",
        "head-ref",
        max_files=10,
        include_content=False,
    )

    assert len(sandbox.commands) == 1
    assert len(result["files"]) == 10
    assert result["files"][-1]["unrenderable"] is True
    assert result["truncated"] is True
    assert result["summary"] == {"files": 15, "additions": 14, "deletions": 2}
    assert all(file["originalContent"] is None for file in result["files"])
    assert all(file["modifiedContent"] is None for file in result["files"])


async def test_read_turn_diff_includes_contents_by_default() -> None:
    sandbox = _FakeSandbox(
        {
            "head": "head-tree",
            "numstat": "1\t1\tchange.txt\0",
            "nameStatus": "M\0change.txt\0",
            "summary": {"files": 1, "additions": 1, "deletions": 1},
        },
        {
            "change.txt": {
                "base": base64.b64encode(b"before\n").decode(),
                "head": base64.b64encode(b"after\n").decode(),
            }
        },
    )

    result = await read_turn_diff(sandbox, None, "base-ref", "head-ref")

    assert len(sandbox.commands) == 2
    assert "cat-file" in sandbox.commands[1]
    assert result["files"][0]["originalContent"] == "before\n"
    assert result["files"][0]["modifiedContent"] == "after\n"
    assert result["summary"] == {"files": 1, "additions": 1, "deletions": 1}


def test_merge_checkpoint_keeps_the_first_snapshot_for_a_turn() -> None:
    first = merge_checkpoint(None, "msg-1", "refs/open-swe/turns/msg-1", "t0")
    resumed = merge_checkpoint(first, "msg-1", "refs/open-swe/turns/other", "t1")
    second = merge_checkpoint(resumed, "msg-2", "refs/open-swe/turns/msg-2", "t2")

    assert resumed == first
    assert [entry["key"] for entry in second] == ["msg-1", "msg-2"]


def test_merge_checkpoint_preserves_repository_and_plan_mode() -> None:
    entries = merge_checkpoint(
        None,
        "msg-1",
        "refs/open-swe/turns/msg-1",
        "t0",
        repo_path="/workspace/repo",
        plan_mode=True,
    )

    assert entries == [
        {
            "key": "msg-1",
            "ref": "refs/open-swe/turns/msg-1",
            "started_at": "t0",
            "repo_path": "/workspace/repo",
            "plan_mode": True,
            "plan_ref": "refs/open-swe/turns/msg-1",
        }
    ]
    assert merge_checkpoint(entries, "msg-1", "other", "t1") == entries


def test_mark_checkpoint_plan_mode_marks_only_the_requested_turn() -> None:
    entries = [
        {"key": "msg-1", "ref": "ref-1", "started_at": "t0"},
        {
            "key": "msg-2",
            "ref": "ref-2",
            "started_at": "t1",
            "repo_path": "/workspace/repo",
        },
    ]

    marked = mark_checkpoint_plan_mode(entries, "msg-2")

    assert "plan_mode" not in marked[0]
    assert marked[1]["plan_mode"] is True
    assert marked[1]["plan_ref"] == "ref-2"
    assert marked[1]["repo_path"] == "/workspace/repo"


async def test_record_turn_checkpoint_uses_preferred_repo_and_keeps_first_snapshot(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "file.txt").write_text("first\n")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)

    class Sandbox:
        async def aexecute(self, command: str, *, timeout: int):
            result = subprocess.run(
                ["bash", "-lc", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return SimpleNamespace(
                exit_code=result.returncode, output=result.stdout + result.stderr
            )

    sandbox = Sandbox()
    first = await record_turn_checkpoint(
        sandbox,
        str(tmp_path),
        "msg-1",
        repo_path=str(repo),
    )
    (repo / "file.txt").write_text("second\n")
    resumed = await record_turn_checkpoint(
        sandbox,
        str(tmp_path),
        "msg-1",
        repo_path=str(repo),
    )

    assert first == resumed == ("refs/open-swe/turns/msg-1", str(repo))
    snapshot = subprocess.run(
        ["git", "-C", str(repo), "show", "refs/open-swe/turns/msg-1:file.txt"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert snapshot.stdout == "first\n"
