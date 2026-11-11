/**
 * The course: every obstacle and pickup is a pure function of (seed, index), so
 * it never needs storing and every player sees the same course.
 */
import { clamp, divFloor, hash32, modFloor, pick } from "./arith"
import {
  CI_GAP_MAX,
  CI_GAP_MIN,
  CI_PERIOD,
  TOKEN_PERCENT,
  CONFLICT_SECOND_LEFT,
  CONFLICT_SWAY,
  GAP_MAX,
  GAP_MIN,
  LEAD,
  OBSTACLES_PER_LEVEL,
  PICKUP_JITTER,
  SALT,
  SPACING,
  WIDTH,
} from "./rules"

export type ObstacleKind = "pr" | "terminal" | "ci" | "conflict"
export type PowerupKind = "forcePush" | "cacheHit" | "autopilot" | "retry"
export type PickupKind = "token" | PowerupKind

export interface Block {
  /** Course x of the left edge, world units. */
  readonly left: number
  readonly width: number
  /** Gap center when it doesn't move. */
  readonly gap: number
  /** Failing CI: the gap slides up and down with this phase. */
  readonly phase: number | null
}

export interface Pickup {
  readonly kind: PickupKind
  /** Course x, world units. */
  readonly x: number
  /** Offset from the gap center it rides on. */
  readonly offset: number
}

export interface Obstacle {
  readonly index: number
  readonly level: number
  readonly kind: ObstacleKind
  /** In course order; a merge conflict has two. */
  readonly blocks: readonly Block[]
  /** Course x of the last block's right edge; the obstacle is passed once the bird clears it. */
  readonly right: number
  readonly pickup: Pickup | null
}

/** The level an obstacle belongs to: 10 obstacles per level, starting at 1. */
export function levelOf(index: number): number {
  return Math.floor(index / OBSTACLES_PER_LEVEL) + 1
}

/** Levels 2–4 each introduce one type, on the 2nd, 5th and 8th obstacle. */
const INTRODUCED: Readonly<Record<number, ObstacleKind>> = {
  2: "terminal",
  3: "ci",
  4: "conflict",
}

function kindOf(seed: number, index: number, level: number): ObstacleKind {
  if (level >= 5) {
    const k = pick(hash32(seed, index, SALT.type), 0, 8)
    return k <= 2 ? "pr" : k <= 4 ? "terminal" : k <= 6 ? "ci" : "conflict"
  }
  const slot = index - (level - 1) * OBSTACLES_PER_LEVEL
  const introduced = INTRODUCED[level]
  return introduced && (slot === 1 || slot === 4 || slot === 7)
    ? introduced
    : "pr"
}

/** The one obstacle per level that carries a power-up instead of a token. */
export function powerupIndex(seed: number, level: number): number {
  return (
    (level - 1) * OBSTACLES_PER_LEVEL +
    3 +
    pick(hash32(seed, level, SALT.powerupSlot), 0, 4)
  )
}

function powerupKind(seed: number, level: number): PowerupKind {
  const roll = pick(hash32(seed, level, SALT.powerupType), 0, 99)
  return roll < 30
    ? "forcePush"
    : roll < 60
      ? "cacheHit"
      : roll < 85
        ? "autopilot"
        : "retry"
}

function gapDraw(seed: number, index: number): number {
  return pick(hash32(seed, index, SALT.gap), 0, GAP_MAX - GAP_MIN)
}

/**
 * Each gap averages this obstacle's draw with the previous one's, so neighbours
 * are at most half the range apart (200 units) and every gap stays reachable
 * after the narrow air behind a terminal or merge conflict. Do not go back to
 * independent draws: neighbours 400 units apart can't be flown.
 */
export function baseGap(seed: number, index: number): number {
  const previous = gapDraw(seed, Math.max(0, index - 1))
  return GAP_MIN + divFloor(gapDraw(seed, index) + previous, 2)
}

export function obstacle(seed: number, index: number): Obstacle {
  const level = levelOf(index)
  const kind = kindOf(seed, index, level)
  const left = LEAD + index * SPACING
  const drawn = baseGap(seed, index)
  // Like Failing CI, a conflict keeps every gap it shows inside [GAP_MIN, GAP_MAX].
  const gap =
    kind === "conflict"
      ? clamp(drawn, GAP_MIN + CONFLICT_SWAY, GAP_MAX - CONFLICT_SWAY)
      : drawn
  const blocks: readonly Block[] =
    kind === "conflict"
      ? [
          { left, width: WIDTH.pr, gap: gap - CONFLICT_SWAY, phase: null },
          {
            left: left + CONFLICT_SECOND_LEFT,
            width: WIDTH.pr,
            gap: gap + CONFLICT_SWAY,
            phase: null,
          },
        ]
      : [
          {
            left,
            width: WIDTH[kind],
            gap: kind === "ci" ? clamp(gap, CI_GAP_MIN, CI_GAP_MAX) : gap,
            phase:
              kind === "ci"
                ? pick(hash32(seed, index, SALT.ciPhase), 0, CI_PERIOD - 1)
                : null,
          },
        ]
  const last = blocks[blocks.length - 1] ?? blocks[0]
  const right = last ? last.left + last.width : left

  const isPowerup = index === powerupIndex(seed, level)
  const hasToken = pick(hash32(seed, index, SALT.token), 0, 99) < TOKEN_PERCENT
  // A conflict's pickup floats in the open air between its two blocks.
  const x =
    kind === "conflict"
      ? left + (WIDTH.pr + CONFLICT_SECOND_LEFT) / 2
      : left + WIDTH[kind] / 2
  const offset = pick(
    hash32(seed, index, SALT.pickupY),
    -PICKUP_JITTER,
    PICKUP_JITTER
  )
  const pickup: Pickup | null = isPowerup
    ? { kind: powerupKind(seed, level), x, offset }
    : hasToken
      ? { kind: "token", x, offset }
      : null

  return { index, level, kind, blocks, right, pickup }
}

/** Triangle wave in [−90, 90] with period CI_PERIOD; no trig, so both languages agree. */
function triangle(tick: number, phase: number): number {
  const p = modFloor(tick + phase, CI_PERIOD)
  return p < CI_PERIOD / 2 ? 2 * p - 90 : 270 - 2 * p
}

/** A block's gap center on a tick, world units. */
export function gapAt(block: Block, tick: number): number {
  return block.phase === null
    ? block.gap
    : block.gap + triangle(tick, block.phase)
}

/**
 * The gap center an anchored pickup rides on: its block's gap, or for a merge
 * conflict, the obstacle's base gap between the two blocks.
 */
export function pickupY(item: Obstacle, pickup: Pickup, tick: number): number {
  const [first, second] = item.blocks
  const base =
    item.kind === "conflict" && first && second
      ? (first.gap + second.gap) / 2
      : first
        ? gapAt(first, tick)
        : 0
  return base + pickup.offset
}
