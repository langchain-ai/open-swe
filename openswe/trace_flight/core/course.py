"""The course: every obstacle and pickup is a pure function of (seed, index).

Mirrors ui/src/features/trace-flight/core/course.ts and arith.ts. Python's ``//``
and ``%`` already floor, which is what the TypeScript ``divFloor`` and
``modFloor`` reproduce; every 32-bit multiply is masked like ``Math.imul``.
"""

import functools
from dataclasses import dataclass
from typing import Final, Literal

from openswe.trace_flight.core.rules import (
    CI_GAP_MAX,
    CI_GAP_MIN,
    CI_PERIOD,
    CONFLICT_SECOND_LEFT,
    CONFLICT_SWAY,
    GAP_MAX,
    GAP_MIN,
    LEAD,
    OBSTACLES_PER_LEVEL,
    PICKUP_JITTER,
    SALT_CI_PHASE,
    SALT_GAP,
    SALT_PICKUP_Y,
    SALT_POWERUP_SLOT,
    SALT_POWERUP_TYPE,
    SALT_TOKEN,
    SALT_TYPE,
    SPACING,
    TOKEN_PERCENT,
    WIDTH_CI,
    WIDTH_PR,
    WIDTH_TERMINAL,
)

type ObstacleKind = Literal["pr", "terminal", "ci", "conflict"]
type PowerupKind = Literal["forcePush", "cacheHit", "autopilot", "retry"]
type PickupKind = Literal["token", "forcePush", "cacheHit", "autopilot", "retry"]

_U32: Final = 0xFFFFFFFF
_WIDTH: Final[dict[ObstacleKind, int]] = {
    "pr": WIDTH_PR,
    "terminal": WIDTH_TERMINAL,
    "ci": WIDTH_CI,
    "conflict": WIDTH_PR,
}
# Levels 2-4 each introduce one type, on the 2nd, 5th and 8th obstacle.
_INTRODUCED: Final[dict[int, ObstacleKind]] = {2: "terminal", 3: "ci", 4: "conflict"}


@dataclass(frozen=True, slots=True)
class Block:
    left: int
    width: int
    gap: int
    """Gap center when it doesn't move."""
    phase: int | None
    """Failing CI: the gap slides up and down with this phase."""

    def gap_at(self, tick: int) -> int:
        if self.phase is None:
            return self.gap
        p = (tick + self.phase) % CI_PERIOD
        return self.gap + (2 * p - 90 if p < CI_PERIOD // 2 else 270 - 2 * p)


@dataclass(frozen=True, slots=True)
class Pickup:
    kind: PickupKind
    x: int
    offset: int
    """Offset from the gap center it rides on."""


@dataclass(frozen=True, slots=True)
class Obstacle:
    index: int
    level: int
    kind: ObstacleKind
    blocks: tuple[Block, ...]
    right: int
    """Course x of the last block's right edge; the obstacle is passed once the bird clears it."""
    pickup: Pickup | None

    def pickup_y(self, pickup: Pickup, tick: int) -> int:
        """A conflict's pickup rides the base gap between its blocks; others ride their block's gap."""
        if self.kind == "conflict":
            return (self.blocks[0].gap + self.blocks[1].gap) // 2 + pickup.offset
        return self.blocks[0].gap_at(tick) + pickup.offset


@dataclass(frozen=True, slots=True)
class Course:
    seed: int

    @staticmethod
    def mix32(value: int) -> int:
        """lowbias32 integer mixer."""
        x = value & _U32
        x ^= x >> 16
        x = (x * 0x7FEB352D) & _U32
        x ^= x >> 15
        x = (x * 0x846CA68B) & _U32
        x ^= x >> 16
        return x

    def hash(self, index: int, salt: int) -> int:
        return Course.mix32(Course.mix32((self.seed ^ salt) & _U32) ^ index)

    def pick(self, index: int, salt: int, lo: int, hi: int) -> int:
        return lo + self.hash(index, salt) % (hi - lo + 1)

    @staticmethod
    def level_of(index: int) -> int:
        return index // OBSTACLES_PER_LEVEL + 1

    def kind_of(self, index: int, level: int) -> ObstacleKind:
        if level >= 5:
            k = self.pick(index, SALT_TYPE, 0, 8)
            return "pr" if k <= 2 else "terminal" if k <= 4 else "ci" if k <= 6 else "conflict"
        slot = index - (level - 1) * OBSTACLES_PER_LEVEL
        introduced = _INTRODUCED.get(level)
        return introduced if introduced is not None and slot in (1, 4, 7) else "pr"

    def powerup_index(self, level: int) -> int:
        """The one obstacle per level that carries a power-up instead of a token."""
        return (level - 1) * OBSTACLES_PER_LEVEL + 3 + self.pick(level, SALT_POWERUP_SLOT, 0, 4)

    def powerup_kind(self, level: int) -> PowerupKind:
        roll = self.pick(level, SALT_POWERUP_TYPE, 0, 99)
        return (
            "forcePush"
            if roll < 30
            else "cacheHit"
            if roll < 60
            else "autopilot"
            if roll < 85
            else "retry"
        )

    def base_gap(self, index: int) -> int:
        """Averages this obstacle's draw with the previous one's, so neighbours stay reachable."""
        draw = self.pick(index, SALT_GAP, 0, GAP_MAX - GAP_MIN)
        previous = self.pick(max(0, index - 1), SALT_GAP, 0, GAP_MAX - GAP_MIN)
        return GAP_MIN + (draw + previous) // 2

    def obstacle(self, index: int) -> Obstacle:
        return Course.cached_obstacle(self.seed, index)

    @staticmethod
    @functools.lru_cache(maxsize=4096)
    def cached_obstacle(seed: int, index: int) -> Obstacle:
        """A replay asks for the same few obstacles every tick; building them dominates its cost."""
        return Course(seed).build(index)

    def build(self, index: int) -> Obstacle:
        level = Course.level_of(index)
        kind = self.kind_of(index, level)
        left = LEAD + index * SPACING
        drawn = self.base_gap(index)
        if kind == "conflict":
            gap = min(max(drawn, GAP_MIN + CONFLICT_SWAY), GAP_MAX - CONFLICT_SWAY)
            blocks = (
                Block(left, WIDTH_PR, gap - CONFLICT_SWAY, None),
                Block(left + CONFLICT_SECOND_LEFT, WIDTH_PR, gap + CONFLICT_SWAY, None),
            )
            x = left + (WIDTH_PR + CONFLICT_SECOND_LEFT) // 2
        elif kind == "ci":
            phase = self.pick(index, SALT_CI_PHASE, 0, CI_PERIOD - 1)
            blocks = (Block(left, WIDTH_CI, min(max(drawn, CI_GAP_MIN), CI_GAP_MAX), phase),)
            x = left + WIDTH_CI // 2
        else:
            blocks = (Block(left, _WIDTH[kind], drawn, None),)
            x = left + _WIDTH[kind] // 2
        last = blocks[-1]

        offset = self.pick(index, SALT_PICKUP_Y, -PICKUP_JITTER, PICKUP_JITTER)
        pickup: Pickup | None = None
        if index == self.powerup_index(level):
            pickup = Pickup(self.powerup_kind(level), x, offset)
        elif self.pick(index, SALT_TOKEN, 0, 99) < TOKEN_PERCENT:
            pickup = Pickup("token", x, offset)
        return Obstacle(index, level, kind, blocks, last.left + last.width, pickup)
