/**
 * A scripted player for tests and the harness: flaps whenever it sinks below the
 * gap it is heading for. It plays through the same input path as a person, so
 * its flaps count against the budget and can be recorded as a run.
 */
import { divFloor } from "./arith"
import { gapAt, obstacle } from "./course"
import { BIRD_X, DEFAULT_TUNING, Q, type Tuning } from "./rules"
import { step } from "./sim"
import type { SimState } from "./state"

export interface BotPlay {
  readonly state: SimState
  /** Ticks on which the bot's flap was accepted. */
  readonly flapTicks: readonly number[]
}

/**
 * Flaps once it sinks `margin` below the gap it is heading for. Through a merge
 * conflict it holds the middle, where both gaps overlap.
 */
export function botWantsFlap(
  state: SimState,
  tuning: Tuning = DEFAULT_TUNING,
  margin = 30
): boolean {
  const dU = divFloor(state.d, Q)
  const next = obstacle(state.seed, state.nextPass)
  const [first, second] = next.blocks
  const ahead = next.blocks.find(
    (block) => block.left + block.width - dU >= BIRD_X - tuning.birdRadius
  )
  const gap =
    next.kind === "conflict" && first && second
      ? (first.gap + second.gap) / 2
      : ahead
        ? gapAt(ahead, state.tick)
        : 450
  return divFloor(state.y, Q) > gap + margin
}

/** Plays from `start` until `until` says stop or the bird dies. */
export function playBot(
  start: SimState,
  until: (state: SimState) => boolean,
  maxTicks: number
): BotPlay {
  let state = start
  const flapTicks: number[] = []
  while (!state.dead && !until(state) && state.tick < maxTicks) {
    const wants = state.tick === 0 || botWantsFlap(state)
    const next = step(state, wants)
    if (next.flaps > state.flaps) flapTicks.push(state.tick)
    state = next
  }
  return { state, flapTicks }
}
