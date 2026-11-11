/**
 * Your best run's flight path, drawn as a faint line to chase. Built once by
 * replaying the run's inputs, so only the inputs are ever stored.
 */
import { divFloor } from "../core/arith"
import type { Run } from "../core/replay"
import { Q } from "../core/rules"
import { step } from "../core/sim"
import { initialState } from "../core/state"

/** Course distance between samples, world units. */
export const TRACE_STEP = 8

export interface Trace {
  /** The bird's y, world units, when the course had scrolled k × TRACE_STEP. */
  readonly ys: Int16Array
}

/**
 * Samples the replayed run by course distance, so the line sits on the course
 * the way the bird flew it. Force push scrolls two samples' worth in a tick;
 * those in between are interpolated.
 */
export function traceOf(run: Run): Trace {
  let at = 0
  const flaps = new Set(run.flapDeltas.map((delta) => (at += delta)))
  let state = initialState(run.seed)
  const ys: number[] = [divFloor(state.y, Q)]
  while (state.tick < run.endTick && !state.dead) {
    const next = step(state, flaps.has(state.tick))
    const from = divFloor(state.d, Q)
    const to = divFloor(next.d, Q)
    const y0 = divFloor(state.y, Q)
    const y1 = divFloor(next.y, Q)
    for (let k = ys.length; k * TRACE_STEP <= to; k++)
      ys.push(
        to === from
          ? y1
          : y0 + divFloor((y1 - y0) * (k * TRACE_STEP - from), to - from)
      )
    state = next
  }
  return { ys: Int16Array.from(ys) }
}
