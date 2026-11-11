"""Sim state and one tick of the sim; mirrors ui/src/features/trace-flight/core/state.ts and sim.ts.

One tick: (1) player input, (2) autopilot, (3) movement, (4) scroll, (5) collisions
and damage (a lethal hit stops the tick here), (6) pickups in obstacle order,
(7) passes and score, (8) effect and invulnerability timers. Integer maths only,
in the same order as the TypeScript core.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Final, Literal, Self

from openswe.trace_flight.core.course import Course, Obstacle, PickupKind
from openswe.trace_flight.core.rules import (
    AUTOPILOT_HANDOFF_CLEAR,
    AUTOPILOT_MARGIN,
    AUTOPILOT_TAKEOVER_TICKS,
    BIRD_RADIUS,
    BIRD_X,
    CEILING,
    CONFLICT_SECOND_LEFT,
    DURATION_AUTOPILOT,
    DURATION_CACHE_HIT,
    DURATION_FORCE_PUSH,
    FLAP_BUDGET,
    FLAP_VY_Q8,
    FLOOR,
    GAP_HALF,
    GRAVITY_Q8,
    INVULNERABLE_TICKS,
    LEAD,
    MAGNET_ACCEL,
    MAGNET_RANGE,
    MAGNET_STEP_MAX,
    MAGNET_STEP_START,
    MAX_FALL_Q8,
    MAX_LIVES,
    POWERUP_RADIUS,
    SCROLL_Q8,
    SPACING,
    START_LIVES,
    START_Y,
    TOKEN_RADIUS,
    WIDTH_PR,
    Q,
)

type EffectKind = Literal["none", "forcePush", "cacheHit", "autopilot"]
type Mode = Literal["play", "forcePush", "autopilot"]

_U32: Final = 0xFFFFFFFF
_WIDEST: Final = CONFLICT_SECOND_LEFT + WIDTH_PR
_EFFECT_CODE: Final[dict[EffectKind, int]] = {
    "none": 0,
    "forcePush": 1,
    "cacheHit": 2,
    "autopilot": 3,
}
_DURATION: Final[dict[PickupKind, int]] = {
    "forcePush": DURATION_FORCE_PUSH,
    "cacheHit": DURATION_CACHE_HIT,
    "autopilot": DURATION_AUTOPILOT,
}


@dataclass(frozen=True, slots=True)
class Effect:
    kind: EffectKind = "none"
    remaining: int = 0

    @property
    def mode(self) -> Mode:
        if self.kind == "forcePush" or self.kind == "autopilot":
            return self.kind
        return "play"

    @property
    def can_take_over(self) -> bool:
        """Whether a flap now ends Autopilot and hands control back."""
        return self.kind == "autopilot" and self.remaining <= AUTOPILOT_TAKEOVER_TICKS

    def ticked(self, open_air: Callable[[], bool]) -> Effect:
        """Counts down; Autopilot holds on its last tick until the bird is in open air."""
        if self.kind == "none":
            return self
        if self.remaining > 1:
            return Effect(self.kind, self.remaining - 1)
        return self if self.kind == "autopilot" and not open_air() else NO_EFFECT


NO_EFFECT: Final = Effect()


@dataclass(frozen=True, slots=True)
class FreePickup:
    """A token the Cache hit magnet has pulled off its obstacle; it no longer moves with the gap."""

    obstacle: int
    x: int
    y: int
    age: int
    """Ticks the magnet has pulled it; its pull speeds up with age."""

    @staticmethod
    def pulled(ox: int, oy: int, age: int) -> tuple[int, int]:
        """One tick of the magnet on a token at offset (ox, oy) from the bird, pulling it straight in."""
        r = max(abs(ox), abs(oy))
        stride = min(MAGNET_STEP_MAX, MAGNET_STEP_START + age * MAGNET_ACCEL)
        if r <= stride:
            return 0, 0
        return ox * (r - stride) // r, oy * (r - stride) // r


@dataclass(frozen=True, slots=True)
class _Candidate:
    obstacle: int
    kind: PickupKind
    x: int
    y: int
    free: bool
    age: int


@dataclass(frozen=True, slots=True)
class SimState:
    seed: int
    tick: int
    y: int
    """Q8, y points down."""
    vy: int
    d: int
    """Course distance scrolled, Q8."""
    lives: int
    score: int
    passed: int
    tokens: int
    flaps: int
    """Accepted player flaps, counted against FLAP_BUDGET."""
    effect: Effect
    invulnerable: int
    dead: bool
    next_pass: int
    collected: tuple[int, ...]
    """Obstacles near the bird whose pickup is gone, ascending."""
    free: tuple[FreePickup, ...]

    @classmethod
    def initial(cls, seed: int) -> Self:
        return cls(
            seed=seed & _U32,
            tick=0,
            y=START_Y * Q,
            vy=0,
            d=0,
            lives=START_LIVES,
            score=0,
            passed=0,
            tokens=0,
            flaps=0,
            effect=NO_EFFECT,
            invulnerable=0,
            dead=False,
            next_pass=0,
            collected=(),
            free=(),
        )

    @property
    def course(self) -> Course:
        return Course(self.seed)

    def state_hash(self) -> int:
        """FNV-1a over every field as int32 little-endian, in the TypeScript core's order."""
        values = [
            self.seed,
            self.tick,
            self.y,
            self.vy,
            self.d,
            self.lives,
            self.score,
            self.passed,
            self.tokens,
            self.flaps,
            _EFFECT_CODE[self.effect.kind],
            self.effect.remaining,
            self.invulnerable,
            1 if self.dead else 0,
            self.next_pass,
            len(self.collected),
            *self.collected,
            len(self.free),
            *(v for p in self.free for v in (p.obstacle, p.x, p.y, p.age)),
        ]
        h = 0x811C9DC5
        for value in values:
            for byte in (value & _U32).to_bytes(4, "little"):
                h = ((h ^ byte) * 0x01000193) & _U32
        return h

    @staticmethod
    def nearby_indices(d_u: int) -> range:
        """Obstacle indices whose blocks or pickups can reach the bird at scroll ``d_u``."""
        lo = max(0, (d_u + BIRD_X - MAGNET_RANGE - _WIDEST - LEAD) // SPACING)
        hi = (d_u + BIRD_X + MAGNET_RANGE - LEAD) // SPACING
        return range(lo, hi + 1)

    @staticmethod
    def out_of_bounds(y_u: int, r: int) -> bool:
        return y_u - r < CEILING or y_u + r > FLOOR

    def hits(self, item: Obstacle, d_u: int, y_u: int, r: int) -> bool:
        """Circle-vs-rect in whole world units, rejecting on the x axis before any squaring."""
        for block in item.blocks:
            left = block.left - d_u
            right = left + block.width
            if BIRD_X + r <= left or BIRD_X - r >= right:
                continue
            gap = block.gap_at(self.tick)
            nx = min(max(BIRD_X, left), right) - BIRD_X
            for top, bottom in ((CEILING, gap - GAP_HALF), (gap + GAP_HALF, FLOOR)):
                ny = min(max(y_u, top), bottom) - y_u
                if nx * nx + ny * ny < r * r:
                    return True
        return False

    def autopilot_flaps(self) -> bool:
        """The autopilot flaps when it sinks below the next gap it hasn't cleared yet."""
        d_u = self.d // Q
        block = next(
            (
                b
                for b in self.course.obstacle(self.next_pass).blocks
                if b.left + b.width - d_u >= BIRD_X - BIRD_RADIUS
            ),
            None,
        )
        target = block.gap_at(self.tick) if block is not None else START_Y
        return self.y // Q > target + AUTOPILOT_MARGIN and self.vy >= 0

    def in_open_air(self, d_u: int, r: int) -> bool:
        """No block overlaps the bird or the stretch just ahead of it."""
        start = BIRD_X - r
        end = BIRD_X + r + AUTOPILOT_HANDOFF_CLEAR
        lo = max(0, (d_u + start - _WIDEST - LEAD) // SPACING)
        hi = (d_u + end - LEAD) // SPACING
        for i in range(lo, hi + 1):
            for block in self.course.obstacle(i).blocks:
                left = block.left - d_u
                if left < end and left + block.width > start:
                    return False
        return True

    def step(self, flap_input: bool) -> SimState:
        if self.dead:
            return self
        r = BIRD_RADIUS
        tick = self.tick
        course = self.course
        y, vy, lives, score, tokens, flaps = (
            self.y,
            self.vy,
            self.lives,
            self.score,
            self.tokens,
            self.flaps,
        )

        # (1) Player input; near Autopilot's end an accepted flap also takes over.
        accepted = flap_input and flaps < FLAP_BUDGET
        current = NO_EFFECT if accepted and self.effect.can_take_over else self.effect
        mode = current.mode
        # (2) The autopilot flies instead while it's on.
        flapped = False
        if mode == "play" and accepted:
            flapped = True
            flaps += 1
        if mode == "autopilot":
            flapped = self.autopilot_flaps()

        # (3) Movement. Force push holds the bird still while the world rushes past.
        if mode == "forcePush":
            vy = 0
        else:
            if flapped:
                vy = FLAP_VY_Q8
            vy = min(vy + GRAVITY_Q8, MAX_FALL_Q8)
            y += vy

        # (4) Scroll.
        d = self.d + SCROLL_Q8 * (2 if mode == "forcePush" else 1)
        d_u = d // Q
        near = [course.obstacle(i) for i in SimState.nearby_indices(d_u)]

        # (5) Collisions. Several hits on one tick cost one life.
        struck = SimState.out_of_bounds(y // Q, r) or any(
            self.hits(item, d_u, y // Q, r) for item in near
        )
        invulnerable = self.invulnerable
        if mode == "play" and self.invulnerable == 0 and struck:
            lives -= 1
            if lives == 0:
                return replace(
                    self, tick=tick + 1, y=y, vy=vy, d=d, lives=lives, flaps=flaps, dead=True
                )
            invulnerable = INVULNERABLE_TICKS
            vy = 0
        if SimState.out_of_bounds(y // Q, r):
            y = min(max(y, r * Q), (FLOOR - r) * Q)
            vy = 0

        # (6) Pickups, in obstacle order. The magnet pulls tokens before collection.
        y_u = y // Q
        collected = set(self.collected)
        freed = {p.obstacle for p in self.free}
        candidates = [_Candidate(p.obstacle, "token", p.x, p.y, True, p.age) for p in self.free]
        for item in near:
            pickup = item.pickup
            if pickup is None or item.index in collected or item.index in freed:
                continue
            candidates.append(
                _Candidate(item.index, pickup.kind, pickup.x, item.pickup_y(pickup, tick), False, 0)
            )
        candidates.sort(key=lambda c: c.obstacle)

        effect: Effect | None = None
        free: list[FreePickup] = []
        for candidate in candidates:
            x, py, moved, age = candidate.x, candidate.y, candidate.free, candidate.age
            if self.effect.kind == "cacheHit" and candidate.kind == "token":
                ox = x - d_u - BIRD_X
                oy = py - y_u
                if abs(ox) <= MAGNET_RANGE and abs(oy) <= MAGNET_RANGE:
                    nx, ny = FreePickup.pulled(ox, oy, age)
                    x = d_u + BIRD_X + nx
                    py = y_u + ny
                    age += 1
                    moved = True
            reach = r + (TOKEN_RADIUS if candidate.kind == "token" else POWERUP_RADIUS)
            ox = x - d_u - BIRD_X
            oy = py - y_u
            if not (abs(ox) < reach and abs(oy) < reach and ox * ox + oy * oy < reach * reach):
                # A moved token far behind the bird can never be reached again.
                if moved and x - d_u >= BIRD_X - MAGNET_RANGE - r:
                    free.append(FreePickup(candidate.obstacle, x, py, age))
                continue
            collected.add(candidate.obstacle)
            if candidate.kind == "token":
                score += 1
                tokens += 1
            elif candidate.kind == "retry":
                if lives < MAX_LIVES:
                    lives += 1
                else:
                    effect = Effect("cacheHit", DURATION_CACHE_HIT)
            else:
                effect = Effect(candidate.kind, _DURATION[candidate.kind])

        # (7) Passes: an obstacle is passed once its last block clears the bird.
        passed, next_pass = self.passed, self.next_pass
        while course.obstacle(next_pass).right - d_u < BIRD_X - r:
            next_pass += 1
            passed += 1
            score += 1

        # (8) Timers. Effects and invulnerability gained this tick start next tick.
        ticked = current.ticked(lambda: self.in_open_air(d_u, r))
        if self.invulnerable > 0:
            invulnerable = self.invulnerable - 1

        lo = near[0].index if near else next_pass
        return SimState(
            seed=self.seed,
            tick=tick + 1,
            y=y,
            vy=vy,
            d=d,
            lives=lives,
            score=score,
            passed=passed,
            tokens=tokens,
            flaps=flaps,
            effect=effect if effect is not None else ticked,
            invulnerable=invulnerable,
            dead=False,
            next_pass=next_pass,
            collected=tuple(sorted(i for i in collected if i >= lo)),
            free=tuple(free),
        )
