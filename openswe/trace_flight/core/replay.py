"""Runs and replays; mirrors ui/src/features/trace-flight/core/replay.ts.

A run is its seed plus the tick of every accepted flap, sent as deltas. The
server replays exactly the ticks the client ran and computes the score itself.
"""

from dataclasses import dataclass
from typing import Final, Literal, Self

from openswe.trace_flight.core.rules import FLAP_BUDGET, TICK_CAP
from openswe.trace_flight.core.state import SimState

type EndReason = Literal["death", "quit", "cap"]
type RejectReason = Literal["bad_end_tick", "bad_flaps", "died_early", "wrong_end_reason"]

CHECKPOINT_EVERY: Final = 600
_END_REASONS: Final[dict[str, EndReason]] = {"death": "death", "quit": "quit", "cap": "cap"}


class RunRejected(ValueError):
    def __init__(self, reason: RejectReason) -> None:
        super().__init__(reason)
        self.reason: RejectReason = reason


@dataclass(frozen=True, slots=True)
class Checkpoint:
    tick: int
    state_hash: int


@dataclass(frozen=True, slots=True)
class Replayed:
    state: SimState
    checkpoints: tuple[Checkpoint, ...]
    """State hash after every CHECKPOINT_EVERY ticks, and after the last tick."""


@dataclass(frozen=True, slots=True)
class Run:
    seed: int
    end_tick: int
    """Ticks executed: the run ran ticks 0 ... end_tick - 1."""
    end_reason: EndReason
    flap_deltas: tuple[int, ...]
    """Starts with 0 (the first flap starts the run); later deltas are >= 1."""

    @classmethod
    def parse(cls, seed: int, end_tick: object, end_reason: object, flap_deltas: object) -> Self:
        """A run from submitted JSON values, judging each number as JavaScript's ``Number.isInteger`` does."""
        tick = cls.whole(end_tick, "bad_end_tick")
        if not isinstance(flap_deltas, list):
            raise RunRejected("bad_flaps")
        deltas = tuple(cls.whole(delta, "bad_flaps") for delta in flap_deltas)
        reason = _END_REASONS.get(end_reason) if isinstance(end_reason, str) else None
        if reason is None:
            raise RunRejected("wrong_end_reason")
        return cls(seed, tick, reason, deltas)

    @staticmethod
    def whole(value: object, reason: RejectReason) -> int:
        """JSON has one number type: ``1.0`` is the integer 1 there, and ``true`` is not a number."""
        if isinstance(value, bool):
            raise RunRejected(reason)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise RunRejected(reason)

    def flap_ticks(self) -> frozenset[int]:
        deltas = self.flap_deltas
        if not deltas or len(deltas) > FLAP_BUDGET or deltas[0] != 0:
            raise RunRejected("bad_flaps")
        ticks: set[int] = set()
        at = 0
        for k, delta in enumerate(deltas):
            if k > 0 and delta < 1:
                raise RunRejected("bad_flaps")
            at += delta
            if at >= self.end_tick:
                raise RunRejected("bad_flaps")
            ticks.add(at)
        return frozenset(ticks)

    def replay(self) -> Replayed:
        if not 1 <= self.end_tick <= TICK_CAP:
            raise RunRejected("bad_end_tick")
        flaps = self.flap_ticks()
        state = SimState.initial(self.seed)
        checkpoints: list[Checkpoint] = []
        for t in range(self.end_tick):
            if state.dead:
                raise RunRejected("died_early")
            state = state.step(t in flaps)
            if state.tick % CHECKPOINT_EVERY == 0 or state.tick == self.end_tick:
                checkpoints.append(Checkpoint(state.tick, state.state_hash()))
        # A log holds accepted flaps only; one on a tick that ignores input is forged.
        if state.flaps != len(flaps):
            raise RunRejected("bad_flaps")
        reason: EndReason = (
            "death" if state.dead else "cap" if self.end_tick == TICK_CAP else "quit"
        )
        if reason != self.end_reason:
            raise RunRejected("wrong_end_reason")
        return Replayed(state, tuple(checkpoints))
