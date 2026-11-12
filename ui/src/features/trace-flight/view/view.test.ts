import { describe, expect, it } from "vitest"

import { divFloor } from "../core/arith"
import { playBot } from "../core/bot"
import { toDeltas, type Run } from "../core/replay"
import { OBSTACLES_PER_LEVEL, Q } from "../core/rules"
import { step } from "../core/sim"
import { initialState } from "../core/state"
import { cameoOf } from "./bufos"
import { boardOf } from "../hosts/lab/scoreStore"
import { TRACE_STEP, traceOf } from "./trace"

describe("trace", () => {
  it("records the bird's height at every step of course distance, through Force push", () => {
    // Seed 7's bot picks up Force push near tick 5270, which scrolls two samples a tick.
    const { state: end, flapTicks } = playBot(
      initialState(7),
      (s) => s.tick >= 5600,
      6000
    )
    const run: Run = {
      seed: 7,
      endTick: end.tick,
      endReason: "quit",
      flapDeltas: toDeltas(flapTicks),
    }
    const { ys } = traceOf(run)
    expect(ys.length).toBe(divFloor(end.d, Q) / TRACE_STEP + 1)
    const flaps = new Set(flapTicks)
    let state = initialState(7)
    let boosted = 0
    while (state.tick < run.endTick) {
      const next = step(state, flaps.has(state.tick))
      const d = divFloor(next.d, Q)
      expect(ys[d / TRACE_STEP]).toBe(divFloor(next.y, Q))
      if (d - divFloor(state.d, Q) > TRACE_STEP) boosted += 1
      state = next
    }
    expect(boosted).toBeGreaterThan(0)
  })
})

describe("board", () => {
  const rivals = [
    { name: "a", score: 10 },
    { name: "b", score: 5 },
    { name: "c", score: 4 },
    { name: "d", score: 3 },
    { name: "e", score: 2 },
    { name: "f", score: 1 },
  ]

  it("ranks a tie below whoever got there first, and adds your own row below the top five", () => {
    expect(boardOf(rivals, 5).map((r) => [r.rank, r.name])).toEqual([
      [1, "a"],
      [2, "b"],
      [3, "you"],
      [4, "c"],
      [5, "d"],
    ])
    expect(boardOf(rivals, 0).at(-1)).toEqual({
      rank: 7,
      name: "you",
      score: 0,
      you: true,
    })
  })
})

describe("Bufo cameos", () => {
  it("greet you first, stay inside their level and never repeat back to back", () => {
    for (const seed of [0, 7, 0xffffffff]) {
      expect(cameoOf(seed, 1).bufo).toBe("wave")
      for (let level = 2; level <= 60; level++) {
        const { index, bufo } = cameoOf(seed, level)
        const slot = index - (level - 1) * OBSTACLES_PER_LEVEL
        expect(slot).toBeGreaterThanOrEqual(2)
        expect(slot).toBeLessThanOrEqual(8)
        expect(bufo).not.toBe(cameoOf(seed, level - 1).bufo)
      }
    }
  })
})
