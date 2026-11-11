/**
 * Runs and replays. A run is its seed plus the tick of every accepted flap, sent
 * as deltas; the server replays exactly the ticks the client ran and computes
 * the score itself.
 */
import { FLAP_BUDGET, TICK_CAP } from "./rules"
import { step } from "./sim"
import { initialState, stateHash, type SimState } from "./state"

export type EndReason = "death" | "quit" | "cap"

export interface Run {
  readonly seed: number
  /** Ticks executed: the run ran ticks 0 … endTick − 1. */
  readonly endTick: number
  readonly endReason: EndReason
  /** Starts with 0 (the first flap starts the run); later deltas are ≥ 1. */
  readonly flapDeltas: readonly number[]
}

export interface Checkpoint {
  readonly tick: number
  readonly hash: number
}

export interface Replayed {
  readonly state: SimState
  /** State hash after every CHECKPOINT_EVERY ticks, and after the last tick. */
  readonly checkpoints: readonly Checkpoint[]
}

export const CHECKPOINT_EVERY = 600

export type RunError =
  | "bad_end_tick"
  | "bad_flaps"
  | "died_early"
  | "wrong_end_reason"

/** Ticks of each flap, or null when the deltas break the submission rules. */
export function flapTicks(run: Run): ReadonlySet<number> | null {
  const { flapDeltas, endTick } = run
  if (flapDeltas.length === 0 || flapDeltas.length > FLAP_BUDGET) return null
  if (flapDeltas[0] !== 0) return null
  const ticks = new Set<number>()
  let at = 0
  for (const [k, delta] of flapDeltas.entries()) {
    if (!Number.isInteger(delta) || (k > 0 && delta < 1)) return null
    at += delta
    if (at >= endTick) return null
    ticks.add(at)
  }
  return ticks
}

export function replay(run: Run): Replayed | RunError {
  if (
    !Number.isInteger(run.endTick) ||
    run.endTick < 1 ||
    run.endTick > TICK_CAP
  )
    return "bad_end_tick"
  const flaps = flapTicks(run)
  if (!flaps) return "bad_flaps"
  let state = initialState(run.seed)
  const checkpoints: Checkpoint[] = []
  for (let t = 0; t < run.endTick; t++) {
    if (state.dead) return "died_early"
    state = step(state, flaps.has(t))
    if (state.tick % CHECKPOINT_EVERY === 0 || state.tick === run.endTick)
      checkpoints.push({ tick: state.tick, hash: stateHash(state) })
  }
  // A log holds accepted flaps only; one on a tick that ignores input is forged.
  if (state.flaps !== flaps.size) return "bad_flaps"
  const reason: EndReason = state.dead
    ? "death"
    : run.endTick === TICK_CAP
      ? "cap"
      : "quit"
  if (reason !== run.endReason) return "wrong_end_reason"
  return { state, checkpoints }
}

/** Turns the ticks of accepted flaps into the deltas a run submits. */
export function toDeltas(ticks: readonly number[]): readonly number[] {
  return ticks.map((tick, k) => tick - (ticks[k - 1] ?? 0))
}
