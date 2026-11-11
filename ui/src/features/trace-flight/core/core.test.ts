import { describe, expect, it } from "vitest"

import { divFloor, modFloor } from "./arith"
import { playBot } from "./bot"
import { baseGap, gapAt, levelOf, obstacle, powerupIndex } from "./course"
import { replay, toDeltas, type Run } from "./replay"
import {
  AUTOPILOT_TAKEOVER_TICKS,
  BIRD_RADIUS,
  BIRD_X,
  CI_PERIOD,
  DURATION,
  FLAP_BUDGET,
  FLOOR,
  GAP_MAX,
  GAP_MIN,
  FLAP_VY_Q8,
  GRAVITY_Q8,
  INVULNERABLE_TICKS,
  Q,
  SCROLL_Q8,
  SPACING,
  START_LIVES,
} from "./rules"
import { canTakeOver, modeOf, step } from "./sim"
import { initialState, stateHash, type SimState } from "./state"

/** Season 1's seed (the season migration) is in the sweep, so its course is known to be flyable. */
const SEASON_1_SEED = 3051132594
const SEEDS = [0, 1, 7, 0xdeadbeef, 0x7fffffff, 0xffffffff, SEASON_1_SEED]

function run(state: SimState, ticks: number, flap = false): SimState {
  let s = state
  for (let t = 0; t < ticks; t++) s = step(s, flap)
  return s
}

describe("arithmetic", () => {
  // Python's // and % floor toward −∞; these must match it, not JS's truncation.
  it("floors toward negative infinity", () => {
    expect(divFloor(-7, 2)).toBe(-4)
    expect(modFloor(-7, 180)).toBe(173)
    expect(divFloor(7, 2)).toBe(3)
  })
})

describe("course", () => {
  it("introduces one new obstacle type per level from 2 to 4", () => {
    const kinds = (level: number) =>
      Array.from(
        { length: 10 },
        (_, k) => obstacle(1, (level - 1) * 10 + k).kind
      )
    expect(new Set(kinds(1))).toEqual(new Set(["pr"]))
    expect(kinds(2).filter((k) => k === "terminal")).toHaveLength(3)
    expect(kinds(3).filter((k) => k === "ci")).toHaveLength(3)
    expect(kinds(4).filter((k) => k === "conflict")).toHaveLength(3)
  })

  it("keeps every gap inside the playable band", () => {
    for (const seed of SEEDS)
      for (let i = 0; i < 300; i++)
        for (const block of obstacle(seed, i).blocks)
          for (let tick = 0; tick < CI_PERIOD; tick += 9) {
            const gap = gapAt(block, tick)
            expect(gap).toBeGreaterThanOrEqual(GAP_MIN)
            expect(gap).toBeLessThanOrEqual(GAP_MAX)
          }
  })

  // Neighbouring gaps further apart can't be flown after a wide obstacle.
  it("keeps neighbouring gaps within half the range of each other", () => {
    for (const seed of SEEDS)
      for (let i = 1; i < 300; i++)
        expect(
          Math.abs(baseGap(seed, i) - baseGap(seed, i - 1))
        ).toBeLessThanOrEqual((GAP_MAX - GAP_MIN) / 2)
  })

  it("places exactly one power-up per level", () => {
    for (const seed of SEEDS)
      for (let level = 1; level <= 25; level++) {
        const indices = Array.from(
          { length: 10 },
          (_, k) => (level - 1) * 10 + k
        )
        const powerups = indices.filter((i) => {
          const pickup = obstacle(seed, i).pickup
          return pickup !== null && pickup.kind !== "token"
        })
        expect(powerups).toEqual([powerupIndex(seed, level)])
        expect(levelOf(powerupIndex(seed, level))).toBe(level)
      }
  })
})

describe("step", () => {
  it("is deterministic and leaves its input untouched", () => {
    const start = initialState(42)
    const frozen = structuredClone(start)
    expect(stateHash(run(start, 400))).toBe(stateHash(run(start, 400)))
    expect(start).toEqual(frozen)
  })

  it("costs a life on hitting the floor, then grants exactly 90 invulnerable ticks", () => {
    let s = initialState(3)
    while (s.lives === START_LIVES) s = step(s, false)
    expect(s.lives).toBe(START_LIVES - 1)
    expect(s.invulnerable).toBe(INVULNERABLE_TICKS)
    const hitTick = s.tick
    while (s.invulnerable > 0) s = step(s, false)
    expect(s.tick - hitTick).toBe(INVULNERABLE_TICKS)
    expect(divFloor(s.y, Q) + BIRD_RADIUS).toBeLessThanOrEqual(FLOOR)
  })

  it("dies when the last life is lost, and stops", () => {
    const dead = run(initialState(3), 2000)
    expect(dead.dead).toBe(true)
    expect(dead.lives).toBe(0)
    expect(step(dead, true)).toBe(dead)
  })

  it("runs a timed effect for exactly its duration, then plays again", () => {
    const start: SimState = {
      ...initialState(5),
      effect: { kind: "forcePush", remaining: DURATION.forcePush },
    }
    let s = start
    let pushed = 0
    while (modeOf(s.effect) === "forcePush") {
      const next = step(s, true)
      expect(next.y).toBe(s.y)
      expect(next.d - s.d).toBe(2 * SCROLL_Q8)
      expect(next.flaps).toBe(0)
      s = next
      pushed += 1
    }
    expect(pushed).toBe(DURATION.forcePush)
  })

  it("lets a flap take over only in Autopilot's last ticks", () => {
    const flying = (remaining: number): SimState => ({
      ...initialState(4),
      effect: { kind: "autopilot", remaining },
    })
    const early = step(flying(AUTOPILOT_TAKEOVER_TICKS + 1), true)
    expect(modeOf(early.effect)).toBe("autopilot")
    expect(early.flaps).toBe(0)
    const late = step(flying(AUTOPILOT_TAKEOVER_TICKS), true)
    expect(late.effect.kind).toBe("none")
    expect(late.flaps).toBe(1)
    expect(late.vy).toBe(FLAP_VY_Q8 + GRAVITY_Q8)
  })

  it("holds Autopilot past its timer until the bird is in open air", () => {
    const block = obstacle(4, 0).blocks[0]!
    const inBlock: SimState = {
      ...initialState(4),
      y: block.gap * Q,
      d: (block.left - BIRD_X) * Q,
      effect: { kind: "autopilot", remaining: 1 },
    }
    let s = step(inBlock, false)
    expect(s.effect).toEqual({ kind: "autopilot", remaining: 1 })
    let held = 1
    while (s.effect.kind === "autopilot") {
      s = step(s, false)
      held += 1
    }
    expect(held).toBeLessThan(SPACING / (SCROLL_Q8 / Q))
    expect(s.lives).toBe(inBlock.lives)
    const clear = { ...inBlock, d: 0 }
    expect(step(clear, false).effect.kind).toBe("none")
  })

  it("accepts the last flap in the budget and ignores every one after it", () => {
    const last: SimState = { ...initialState(9), flaps: FLAP_BUDGET - 1 }
    const spent = step(last, true)
    expect(spent.flaps).toBe(FLAP_BUDGET)
    expect(spent.vy).toBe(FLAP_VY_Q8 + GRAVITY_Q8)
    const ignored = step(spent, true)
    expect(ignored.flaps).toBe(FLAP_BUDGET)
    expect(ignored.vy).toBe(spent.vy + GRAVITY_Q8)
    const flying: SimState = {
      ...spent,
      effect: { kind: "autopilot", remaining: AUTOPILOT_TAKEOVER_TICKS },
    }
    expect(step(flying, true).effect.kind).toBe("autopilot")
  })
})

describe("survivability", () => {
  // A smoke test that each level's course can be flown, not a proof for every seed.
  it("lets a scripted player clear levels 1–25", () => {
    for (const seed of SEEDS) {
      const { state } = playBot(
        initialState(seed),
        (s) => s.passed >= 250,
        60_000
      )
      expect({ seed, passed: state.passed, lives: state.lives }).toEqual({
        seed,
        passed: 250,
        lives: expect.any(Number),
      })
      expect(state.dead).toBe(false)
    }
  })

  it("counts every pass and token in the score", () => {
    const { state } = playBot(initialState(11), (s) => s.passed >= 60, 20_000)
    expect(state.score).toBe(state.passed + state.tokens)
    expect(state.tokens).toBeGreaterThan(0)
  })
})

describe("replay", () => {
  const played = playBot(initialState(13), (s) => s.passed >= 40, 20_000)
  const quit: Run = {
    seed: 13,
    endTick: played.state.tick,
    endReason: "quit",
    flapDeltas: toDeltas(played.flapTicks),
  }

  it("reproduces a recorded run exactly", () => {
    const result = replay(quit)
    if (typeof result === "string") throw new Error(result)
    expect(stateHash(result.state)).toBe(stateHash(played.state))
    expect(result.state.score).toBe(played.state.score)
  })

  it("rejects a flap logged on a tick that ignores input", () => {
    const { state, flapTicks } = playBot(
      initialState(7),
      (s) => modeOf(s.effect) !== "play" && !canTakeOver(s.effect),
      60_000
    )
    const forged: Run = {
      seed: 7,
      endTick: state.tick + 1,
      endReason: "quit",
      flapDeltas: toDeltas([...flapTicks, state.tick]),
    }
    expect(replay(forged)).toBe("bad_flaps")
  })

  it("rejects malformed and inconsistent runs", () => {
    expect(
      replay({ ...quit, flapDeltas: [1, ...quit.flapDeltas.slice(1)] })
    ).toBe("bad_flaps")
    expect(replay({ ...quit, flapDeltas: [0, 0] })).toBe("bad_flaps")
    expect(replay({ ...quit, endTick: 0 })).toBe("bad_end_tick")
    expect(replay({ ...quit, endReason: "death" })).toBe("wrong_end_reason")
    const death: Run = {
      seed: 3,
      endTick: 5000,
      endReason: "death",
      flapDeltas: [0],
    }
    expect(replay(death)).toBe("died_early")
  })
})
