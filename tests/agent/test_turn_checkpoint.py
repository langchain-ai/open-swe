"""Turn checkpoints: git plumbing output parsing and checkpoint bookkeeping."""

from __future__ import annotations

import base64

from agent.utils.file_diff import build_file_diff
from agent.utils.turn_checkpoint import build_diff_files, merge_checkpoint


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


def test_build_file_diff_applies_the_edit_to_the_before_image() -> None:
    args = {"file_path": "/repo/a.py", "old_string": "OLD", "new_string": "NEW"}

    assert build_file_diff("edit_file", args, "x OLD y OLD", None) == {
        "filePath": "/repo/a.py",
        "originalContent": "x OLD y OLD",
        "newContent": "x NEW y OLD",
        "isNewFile": False,
    }
    assert build_file_diff("edit_file", args, "no match here", None) is None
