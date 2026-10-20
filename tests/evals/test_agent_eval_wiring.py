"""Wiring tests for the agent eval runner: aevaluate call shape and aggregates."""

import os
import sys

from evals.agent import judge, run_eval


async def test_main_wires_target_evaluators_and_cleans_up_threads(monkeypatch, tmp_path) -> None:
    toml = tmp_path / "config.toml"
    toml.write_text('dataset_name = "ds"\nexperiment_prefix = "exp"\nmax_concurrency = 3\n')
    monkeypatch.setattr(run_eval, "CONFIG_PATH", toml)
    for key in ("AGENT_EVAL_DATASET_NAME", "LANGSMITH_PROJECT", "LANGSMITH_TRACING"):
        monkeypatch.delenv(key, raising=False)

    captured: dict = {}

    async def fake_aevaluate(target, **kwargs):
        captured["target"] = target
        captured.update(kwargs)

    deleted: list[str] = []

    class _FakeThreads:
        @staticmethod
        async def delete(thread_id: str) -> None:
            deleted.append(thread_id)

    class _FakeSdk:
        threads = _FakeThreads

    monkeypatch.setattr(run_eval, "aevaluate", fake_aevaluate)
    monkeypatch.setattr(run_eval, "get_client", lambda url=None: _FakeSdk())
    monkeypatch.setattr(run_eval, "drain_thread_ids", lambda: {"t1", "t2"})
    monkeypatch.setattr(sys, "argv", ["run_eval", "--langgraph-url", "http://localhost:2024"])

    await run_eval.main()

    assert captured["target"] is run_eval.solve_task
    assert captured["evaluators"] == [judge.judge_resolution]
    assert captured["summary_evaluators"] == [judge.aggregate_resolution]
    assert captured["experiment_prefix"] == "exp"
    assert captured["max_concurrency"] == 3
    assert captured["data"] == "ds"
    assert os.environ["LANGSMITH_PROJECT"] == "open-swe-evals"
    # drain_thread_ids returns a set; cleanup order is not part of the contract.
    assert set(deleted) == {"t1", "t2"}


def test_aggregate_resolution_reports_rates_and_medians() -> None:
    with judge._records_lock:
        judge._records.clear()
        judge._records.extend(
            [
                {
                    "instance_id": "a",
                    "repo": "r/r",
                    "resolved": True,
                    "patch_applied": True,
                    "fail_to_pass_rate": 1.0,
                    "pass_to_pass_rate": 1.0,
                    "steps": 10,
                    "wall_clock_seconds": 100.0,
                    "error": "",
                },
                {
                    "instance_id": "b",
                    "repo": "r/r",
                    "resolved": False,
                    "patch_applied": False,
                    "fail_to_pass_rate": 0.5,
                    "pass_to_pass_rate": 1.0,
                    "steps": 30,
                    "wall_clock_seconds": 300.0,
                    "error": "install command failed",
                },
            ]
        )

    summary = judge.aggregate_resolution([], [])
    scores = {entry["key"]: entry["score"] for entry in summary["results"]}

    assert scores["resolve_rate"] == 0.5
    assert scores["patch_applied_rate"] == 0.5
    assert scores["error_rate"] == 0.5
    assert scores["mean_fail_to_pass_rate"] == 0.75
    assert scores["mean_pass_to_pass_rate"] == 1.0
    assert scores["median_steps"] == 20.0
    assert scores["median_wall_clock_seconds"] == 200.0


def test_aggregate_resolution_without_records_is_empty() -> None:
    with judge._records_lock:
        judge._records.clear()
    assert judge.aggregate_resolution([], []) == {"results": []}
