"""Turn checkpoints: git plumbing output parsing and checkpoint bookkeeping."""

from __future__ import annotations

import base64
import subprocess
from types import SimpleNamespace

from agent.utils.file_diff import build_file_diff
from agent.utils.turn_checkpoint import (
    build_diff_files,
    mark_checkpoint_plan_mode,
    merge_checkpoint,
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


def test_build_file_diff_applies_the_edit_to_the_before_image() -> None:
    args = {"file_path": "/repo/a.py", "old_string": "OLD", "new_string": "NEW"}

    assert build_file_diff("edit_file", args, "x OLD y OLD", None) == {
        "filePath": "/repo/a.py",
        "originalContent": "x OLD y OLD",
        "newContent": "x NEW y OLD",
        "isNewFile": False,
    }
    assert build_file_diff("edit_file", args, "no match here", None) is None
