"""Instance-file loading tests for the agent eval dataset builder."""

import json
import sys

from evals.agent import build_dataset

_VALID = {
    "instance_id": "probe__repo-1",
    "repo": "probe/repo",
    "base_commit": "a" * 40,
    "problem_statement": "add() subtracts.",
    "fail_to_pass": ["test_calc.py::test_add"],
}


def test_load_instances_keeps_valid_rows_and_reports_bad_ones(tmp_path, capsys) -> None:
    instances = tmp_path / "instances.jsonl"
    rows = [
        _VALID,
        {"instance_id": "broken", "repo": "probe/repo"},  # missing fields
        ["not", "an", "object"],
        dict(_VALID, instance_id="probe__repo-2", fail_to_pass=[]),  # empty fail_to_pass
    ]
    instances.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot json at all\n")

    examples = build_dataset.load_instances(instances)

    assert [example["inputs"]["instance_id"] for example in examples] == ["probe__repo-1"]
    stderr = capsys.readouterr().err
    assert "base_commit" in stderr
    assert "fail_to_pass" in stderr
    assert "invalid JSON" in stderr
    assert "not a JSON object" in stderr


def test_load_instances_skips_blank_lines(tmp_path) -> None:
    instances = tmp_path / "instances.jsonl"
    instances.write_text("\n" + json.dumps(_VALID) + "\n\n")
    assert len(build_dataset.load_instances(instances)) == 1


def test_dry_run_writes_examples_without_uploading(tmp_path, monkeypatch) -> None:
    instances = tmp_path / "instances.jsonl"
    instances.write_text(json.dumps(_VALID) + "\n")
    monkeypatch.setattr(sys, "argv", ["build_dataset", "--instances", str(instances), "--dry-run"])
    monkeypatch.setattr(build_dataset, "upload", lambda dataset_name, examples: None)

    build_dataset.main()

    written = json.loads((tmp_path / "dataset_dryrun.json").read_text())
    assert written[0]["inputs"]["instance_id"] == "probe__repo-1"
    assert written[0]["outputs"]["fail_to_pass"] == ["test_calc.py::test_add"]
