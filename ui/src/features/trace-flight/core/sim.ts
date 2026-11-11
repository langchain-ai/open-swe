/**
 * One sim tick: (1) player input, (2) autopilot, (3) movement, (4) scroll,
 * (5) collisions and damage (a lethal hit stops the tick here), (6) pickups in
 * obstacle order, (7) passes and score, (8) effect and invulnerability timers.
 * Integer maths only; the Python core runs the same steps in the same order.
 */
import { clamp, divFloor } from "./arith"
import { gapAt, obstacle, pickupY, type Obstacle } from "./course"
import {
  AUTOPILOT_HANDOFF_CLEAR,
  AUTOPILOT_MARGIN,
  AUTOPILOT_TAKEOVER_TICKS,
  DEFAULT_TUNING,
  BIRD_X,
  CEILING,
  TOKEN_RADIUS,
  CONFLICT_SECOND_LEFT,
  DURATION,
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
  Q,
  SCROLL_Q8,
  SPACING,
  START_Y,
  WIDTH,
  type Tuning,
} from "./rules"
import type { Effect, FreePickup, SimState } from "./state"

export type Mode = "play" | "forcePush" | "autopilot"

export function modeOf(effect: Effect): Mode {
  return effect.kind === "forcePush" || effect.kind === "autopilot"
    ? effect.kind
    : "play"
}

const WIDEST = CONFLICT_SECOND_LEFT + WIDTH.pr
const NO_EFFECT: Effect = { kind: "none" }

/** Obstacle indices whose blocks or pickups can reach the bird at scroll `dU`. */
function nearbyIndices(dU: number): readonly number[] {
  const lo = Math.max(
    0,
    divFloor(dU + BIRD_X - MAGNET_RANGE - WIDEST - LEAD, SPACING)
  )
  const hi = divFloor(dU + BIRD_X + MAGNET_RANGE - LEAD, SPACING)
  return hi < lo ? [] : Array.from({ length: hi - lo + 1 }, (_, k) => lo + k)
}

/** Circle-vs-rect in whole world units, rejecting on the x axis before any squaring. */
function hitsBlock(
  item: Obstacle,
  tick: number,
  dU: number,
  yU: number,
  r: number
) {
  return item.blocks.some((block) => {
    const left = block.left - dU
    const right = left + block.width
    if (BIRD_X + r <= left || BIRD_X - r >= right) return false
    const gap = gapAt(block, tick)
    const nx = clamp(BIRD_X, left, right) - BIRD_X
    const overlaps = (top: number, bottom: number) => {
      const ny = clamp(yU, top, bottom) - yU
      return nx * nx + ny * ny < r * r
    }
    return overlaps(CEILING, gap - GAP_HALF) || overlaps(gap + GAP_HALF, FLOOR)
  })
}

/** The autopilot flaps when it sinks below the next gap it hasn't cleared yet. */
function autopilotFlaps(state: SimState, tuning: Tuning): boolean {
  const dU = divFloor(state.d, Q)
  const block = obstacle(state.seed, state.nextPass).blocks.find(
    (b) => b.left + b.width - dU >= BIRD_X - tuning.birdRadius
  )
  const target = block ? gapAt(block, state.tick) : START_Y
  return divFloor(state.y, Q) > target + AUTOPILOT_MARGIN && state.vy >= 0
}

/** Whether a flap now ends Autopilot and hands control back. */
export function canTakeOver(effect: Effect): boolean {
  return (
    effect.kind === "autopilot" && effect.remaining <= AUTOPILOT_TAKEOVER_TICKS
  )
}

/** No block overlaps the bird or the stretch just ahead of it. */
function inOpenAir(seed: number, dU: number, r: number): boolean {
  const from = BIRD_X - r
  const to = BIRD_X + r + AUTOPILOT_HANDOFF_CLEAR
  const lo = Math.max(0, divFloor(dU + from - WIDEST - LEAD, SPACING))
  const hi = divFloor(dU + to - LEAD, SPACING)
  for (let i = lo; i <= hi; i++)
    for (const block of obstacle(seed, i).blocks) {
      const left = block.left - dU
      if (left < to && left + block.width > from) return false
    }
  return true
}

/** Counts a timed effect down; Autopilot holds on its last tick until the bird is in open air. */
function tickEffect(effect: Effect, openAir: () => boolean): Effect {
  if (effect.kind === "none") return effect
  if (effect.remaining > 1)
    return { kind: effect.kind, remaining: effect.remaining - 1 }
  return effect.kind === "autopilot" && !openAir() ? effect : NO_EFFECT
}

interface Candidate {
  readonly obstacle: number
  readonly kind: NonNullable<Obstacle["pickup"]>["kind"]
  readonly x: number
  readonly y: number
  readonly free: boolean
  readonly age: number
}

/**
 * One tick of the magnet on a token at offset (ox, oy) from the bird, pulling it
 * straight in. Returns the new offset; (0, 0) once the step covers the rest.
 */
function magnetPull(
  ox: number,
  oy: number,
  age: number
): readonly [number, number] {
  const r = Math.max(Math.abs(ox), Math.abs(oy))
  const stride = Math.min(
    MAGNET_STEP_MAX,
    MAGNET_STEP_START + age * MAGNET_ACCEL
  )
  if (r <= stride) return [0, 0]
  return [divFloor(ox * (r - stride), r), divFloor(oy * (r - stride), r)]
}

export function step(
  state: SimState,
  flapInput: boolean,
  tuning: Tuning = DEFAULT_TUNING
): SimState {
  if (state.dead) return state
  const R = tuning.birdRadius
  const tick = state.tick
  let { y, vy, lives, score, tokens, flaps } = state

  // (1) Player input; near Autopilot's end an accepted flap also takes over.
  const accepted = flapInput && flaps < FLAP_BUDGET
  const current =
    accepted && canTakeOver(state.effect) ? NO_EFFECT : state.effect
  const mode = modeOf(current)
  // (2) The autopilot flies instead while it's on.
  let flapped = false
  if (mode === "play" && accepted) {
    flapped = true
    flaps += 1
  }
  if (mode === "autopilot") flapped = autopilotFlaps(state, tuning)

  // (3) Movement. Force push holds the bird still while the world rushes past.
  if (mode === "forcePush") vy = 0
  else {
    if (flapped) vy = FLAP_VY_Q8
    vy = Math.min(vy + GRAVITY_Q8, MAX_FALL_Q8)
    y += vy
  }

  // (4) Scroll.
  const d = state.d + SCROLL_Q8 * (mode === "forcePush" ? 2 : 1)
  const dU = divFloor(d, Q)
  const near = nearbyIndices(dU).map((i) => obstacle(state.seed, i))

  // (5) Collisions. Several hits on one tick cost one life.
  const outOfBounds = (yU: number) => yU - R < CEILING || yU + R > FLOOR
  const struck =
    outOfBounds(divFloor(y, Q)) ||
    near.some((item) => hitsBlock(item, tick, dU, divFloor(y, Q), R))
  const damageable = mode === "play" && state.invulnerable === 0
  let invulnerable = state.invulnerable
  if (damageable && struck) {
    lives -= 1
    if (lives === 0)
      return { ...state, tick: tick + 1, y, vy, d, lives, flaps, dead: true }
    invulnerable = INVULNERABLE_TICKS
    vy = 0
  }
  if (outOfBounds(divFloor(y, Q))) {
    y = clamp(y, R * Q, (FLOOR - R) * Q)
    vy = 0
  }

  // (6) Pickups, in obstacle order. The magnet pulls tokens before collection.
  const yU = divFloor(y, Q)
  const collected = new Set(state.collected)
  const freed = new Set(state.free.map((p) => p.obstacle))
  const candidates: Candidate[] = []
  for (const p of state.free)
    candidates.push({ ...p, kind: "token", free: true })
  for (const item of near) {
    const pickup = item.pickup
    if (!pickup || collected.has(item.index) || freed.has(item.index)) continue
    candidates.push({
      obstacle: item.index,
      kind: pickup.kind,
      x: pickup.x,
      y: pickupY(item, pickup, tick),
      free: false,
      age: 0,
    })
  }
  candidates.sort((a, b) => a.obstacle - b.obstacle)

  let effect: Effect | null = null
  const free: FreePickup[] = []
  for (const candidate of candidates) {
    let { x, y: py, free: moved, age } = candidate
    if (state.effect.kind === "cacheHit" && candidate.kind === "token") {
      const ox = x - dU - BIRD_X
      const oy = py - yU
      if (Math.abs(ox) <= MAGNET_RANGE && Math.abs(oy) <= MAGNET_RANGE) {
        const [nx, ny] = magnetPull(ox, oy, age)
        x = dU + BIRD_X + nx
        py = yU + ny
        age += 1
        moved = true
      }
    }
    const reach =
      R + (candidate.kind === "token" ? TOKEN_RADIUS : POWERUP_RADIUS)
    const ox = x - dU - BIRD_X
    const oy = py - yU
    const touching =
      Math.abs(ox) < reach &&
      Math.abs(oy) < reach &&
      ox * ox + oy * oy < reach * reach
    if (!touching) {
      // A moved token far behind the bird can never be reached again.
      if (moved && x - dU >= BIRD_X - MAGNET_RANGE - R)
        free.push({ obstacle: candidate.obstacle, x, y: py, age })
      continue
    }
    collected.add(candidate.obstacle)
    if (candidate.kind === "token") {
      score += 1
      tokens += 1
    } else if (candidate.kind === "retry") {
      if (lives < MAX_LIVES) lives += 1
      else effect = { kind: "cacheHit", remaining: DURATION.cacheHit }
    } else
      effect = { kind: candidate.kind, remaining: DURATION[candidate.kind] }
  }

  // (7) Passes: an obstacle is passed once its last block clears the bird.
  let { passed, nextPass } = state
  while (obstacle(state.seed, nextPass).right - dU < BIRD_X - R) {
    nextPass += 1
    passed += 1
    score += 1
  }

  // (8) Timers. Effects and invulnerability gained this tick start next tick.
  const ticked = tickEffect(current, () => inOpenAir(state.seed, dU, R))
  if (state.invulnerable > 0) invulnerable = state.invulnerable - 1

  const lo = near[0]?.index ?? nextPass
  return {
    seed: state.seed,
    tick: tick + 1,
    y,
    vy,
    d,
    lives,
    score,
    passed,
    tokens,
    flaps,
    effect: effect ?? ticked,
    invulnerable,
    dead: false,
    nextPass,
    collected: [...collected].filter((i) => i >= lo).sort((a, b) => a - b),
    free,
  }
}
