import { fnv1a } from "./arith"
import { Q, START_LIVES, START_Y } from "./rules"

export type TimedEffect = "forcePush" | "cacheHit" | "autopilot"

export type Effect =
  | { readonly kind: "none" }
  | { readonly kind: TimedEffect; readonly remaining: number }

/** A token the Cache hit magnet has pulled off its obstacle; it no longer moves with the gap. */
export interface FreePickup {
  readonly obstacle: number
  /** Course x, world units. */
  readonly x: number
  /** World units. */
  readonly y: number
  /** Ticks the magnet has pulled it; its pull speeds up with age. */
  readonly age: number
}

export interface SimState {
  readonly seed: number
  /** Ticks executed so far. */
  readonly tick: number
  /** Q8, y points down. */
  readonly y: number
  readonly vy: number
  /** Course distance scrolled, Q8. */
  readonly d: number
  readonly lives: number
  readonly score: number
  readonly passed: number
  readonly tokens: number
  /** Accepted player flaps, counted against FLAP_BUDGET. */
  readonly flaps: number
  readonly effect: Effect
  /** Ticks left of post-hit invulnerability. */
  readonly invulnerable: number
  readonly dead: boolean
  /** Index of the next obstacle to pass. */
  readonly nextPass: number
  /** Obstacles near the bird whose pickup is gone, ascending. */
  readonly collected: readonly number[]
  /** Pickups the magnet has moved, in obstacle order. */
  readonly free: readonly FreePickup[]
}

export function initialState(seed: number): SimState {
  return {
    seed: seed >>> 0,
    tick: 0,
    y: START_Y * Q,
    vy: 0,
    d: 0,
    lives: START_LIVES,
    score: 0,
    passed: 0,
    tokens: 0,
    flaps: 0,
    effect: { kind: "none" },
    invulnerable: 0,
    dead: false,
    nextPass: 0,
    collected: [],
    free: [],
  }
}

/** Shown as the level number: every 10 obstacles passed. */
export function currentLevel(state: SimState): number {
  return Math.floor(state.passed / 10) + 1
}

const EFFECT_CODE: Readonly<Record<Effect["kind"], number>> = {
  none: 0,
  forcePush: 1,
  cacheHit: 2,
  autopilot: 3,
}

/**
 * FNV-1a over every field, as int32 in this fixed order. The Python core hashes
 * the same order; a golden run's checkpoints compare these to find the first
 * tick the two cores disagree.
 */
export function stateHash(state: SimState): number {
  return fnv1a([
    state.seed,
    state.tick,
    state.y,
    state.vy,
    state.d,
    state.lives,
    state.score,
    state.passed,
    state.tokens,
    state.flaps,
    EFFECT_CODE[state.effect.kind],
    state.effect.kind === "none" ? 0 : state.effect.remaining,
    state.invulnerable,
    state.dead ? 1 : 0,
    state.nextPass,
    state.collected.length,
    ...state.collected,
    state.free.length,
    ...state.free.flatMap((p) => [p.obstacle, p.x, p.y, p.age]),
  ])
}
