"""The Python core against the golden runs the TypeScript core recorded (core/golden.test.ts)."""

import json
from dataclasses import replace
from itertools import accumulate
from pathlib import Path

import pytest

from openswe.trace_flight.core.replay import Run, RunRejected
from openswe.trace_flight.core.rules import (
    AUTOPILOT_TAKEOVER_TICKS,
    FLAP_BUDGET,
    FLAP_VY_Q8,
    GRAVITY_Q8,
    RULES_VERSION,
)
from openswe.trace_flight.core.state import Effect, SimState

GOLDEN = Path(__file__).parent / "golden"


def load(path: Path) -> tuple[Run, dict[str, object]]:
    golden = json.loads(path.read_text())
    assert golden["rulesVersion"] == RULES_VERSION
    run = Run.parse(golden["seed"], golden["endTick"], golden["endReason"], golden["flapDeltas"])
    return run, golden["expected"]


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.json")), ids=lambda p: p.stem)
def test_golden_run_reaches_the_typescript_checkpoints(path: Path) -> None:
    run, expected = load(path)
    result = run.replay()
    assert {
        "score": result.state.score,
        "passed": result.state.passed,
        "lives": result.state.lives,
        "checkpoints": [{"tick": c.tick, "stateHash": c.state_hash} for c in result.checkpoints],
    } == expected


def test_replay_rejects_malformed_and_inconsistent_runs() -> None:
    run, _ = load(GOLDEN / "autopilot-takeover-seed-1.json")
    deltas = list(run.flap_deltas)
    # JSON numbers are judged as JavaScript reads them, so both cores accept the same submissions.
    assert Run.parse(run.seed, float(run.end_tick), "quit", [float(d) for d in deltas]) == run
    cases: list[tuple[object, object, object, str]] = [
        (run.end_tick, "quit", [1, *deltas[1:]], "bad_flaps"),
        (run.end_tick, "quit", [0, 0], "bad_flaps"),
        (run.end_tick, "quit", [0, run.end_tick], "bad_flaps"),
        (run.end_tick, "quit", [0, 1.5], "bad_flaps"),
        (run.end_tick, "quit", [0, True], "bad_flaps"),
        (0, "quit", deltas, "bad_end_tick"),
        (True, "quit", deltas, "bad_end_tick"),
        (run.end_tick, "death", deltas, "wrong_end_reason"),
        (run.end_tick, "fly", deltas, "wrong_end_reason"),
    ]
    for end_tick, end_reason, flap_deltas, reason in cases:
        with pytest.raises(RunRejected) as rejected:
            Run.parse(run.seed, end_tick, end_reason, flap_deltas).replay()
        assert rejected.value.reason == reason
    with pytest.raises(RunRejected) as rejected:
        Run(3, 5000, "death", (0,)).replay()
    assert rejected.value.reason == "died_early"


def test_replay_rejects_a_flap_logged_on_a_tick_that_ignores_input() -> None:
    run, _ = load(GOLDEN / "cap-seed-7.json")
    flaps = frozenset(accumulate(run.flap_deltas))
    state = SimState.initial(run.seed)
    while state.effect.mode == "play" or state.effect.can_take_over:
        state = state.step(state.tick in flaps)
    logged = sorted(t for t in flaps if t < state.tick) + [state.tick]
    deltas = (0, *(b - a for a, b in zip(logged, logged[1:], strict=False)))
    with pytest.raises(RunRejected) as rejected:
        Run(run.seed, state.tick + 1, "quit", deltas).replay()
    assert rejected.value.reason == "bad_flaps"


def test_flap_budget_accepts_the_last_flap_and_ignores_every_one_after_it() -> None:
    spent = replace(SimState.initial(9), flaps=FLAP_BUDGET - 1).step(True)
    assert (spent.flaps, spent.vy) == (FLAP_BUDGET, FLAP_VY_Q8 + GRAVITY_Q8)
    ignored = spent.step(True)
    assert (ignored.flaps, ignored.vy) == (FLAP_BUDGET, spent.vy + GRAVITY_Q8)
    flying = replace(spent, effect=Effect("autopilot", AUTOPILOT_TAKEOVER_TICKS))
    assert flying.step(True).effect.kind == "autopilot"
