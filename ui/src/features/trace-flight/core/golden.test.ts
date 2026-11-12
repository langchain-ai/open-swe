/**
 * Golden runs: the cross-language contract. The Python core replays the same
 * files (tests/trace_flight/test_core.py) and must reach the same checkpoints.
 * After a rules change, re-record with `TRACE_FLIGHT_RECORD=1 pnpm exec vitest
 * run golden` and open a new season; never edit the files by hand.
 */
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import { describe, expect, it } from "vitest"

import { botWantsFlap } from "./bot"
import { obstacle, type ObstacleKind } from "./course"
import { replay, toDeltas, type EndReason, type Run } from "./replay"
import { RULES_VERSION, TICK_CAP } from "./rules"
import { canTakeOver, step } from "./sim"
import { initialState, type SimState, type TimedEffect } from "./state"

interface Golden extends Run {
  readonly rulesVersion: number
  readonly expected: {
    readonly score: number
    readonly passed: number
    readonly lives: number
    readonly checkpoints: readonly {
      readonly tick: number
      readonly stateHash: number
    }[]
  }
}

type Player = (state: SimState) => boolean

interface Recipe {
  readonly seed: number
  /** The run quits here unless the bird dies first. */
  readonly ticks: number
  readonly player: Player
}

const bot: Player = (s) => botWantsFlap(s)
/** Plays like the bot but lets go of the controls in each window, so it takes hits. */
const stumbling =
  (every: number, idle: number): Player =>
  (s) =>
    s.tick % every >= idle && botWantsFlap(s)
/** Never taps while Autopilot flies, so Autopilot runs out and holds until open air. */
const handsOff: Player = (s) => s.effect.kind !== "autopilot" && bot(s)
/** Taps the moment the take-over window opens. */
const eager: Player = (s) =>
  s.effect.kind === "autopilot" ? canTakeOver(s.effect) : bot(s)

const RECIPES: Readonly<Record<string, Recipe>> = {
  "cap-seed-7": { seed: 7, ticks: TICK_CAP, player: bot },
  "seed-max-uint32": { seed: 0xffffffff, ticks: 9_000, player: bot },
  "seed-high-bit": { seed: 0x80000000, ticks: 9_000, player: bot },
  "autopilot-takeover-seed-1": { seed: 1, ticks: 3_000, player: eager },
  "autopilot-hold-seed-1": { seed: 1, ticks: 3_000, player: handsOff },
  "stumbles-seed-21": {
    seed: 21,
    ticks: 40_000,
    player: stumbling(1_500, 50),
  },
  "no-input-seed-0": { seed: 0, ticks: 2_000, player: () => false },
}

const GOLDEN_DIR = new URL(
  "../../../../../tests/trace_flight/golden/",
  import.meta.url
)
const RECORD = process.env.TRACE_FLIGHT_RECORD === "1"

/** Plays a recipe through the same input path as a person; the first flap starts the run. */
function record({ seed, ticks, player }: Recipe): Run {
  let state = initialState(seed)
  const flapTicks: number[] = []
  while (!state.dead && state.tick < Math.min(ticks, TICK_CAP)) {
    const next = step(state, state.tick === 0 || player(state))
    if (next.flaps > state.flaps) flapTicks.push(state.tick)
    state = next
  }
  const endReason: EndReason = state.dead
    ? "death"
    : state.tick === TICK_CAP
      ? "cap"
      : "quit"
  return {
    seed,
    endTick: state.tick,
    endReason,
    flapDeltas: toDeltas(flapTicks),
  }
}

function goldenOf(run: Run): Golden {
  const result = replay(run)
  if (typeof result === "string") throw new Error(result)
  return {
    rulesVersion: RULES_VERSION,
    ...run,
    expected: {
      score: result.state.score,
      passed: result.state.passed,
      lives: result.state.lives,
      checkpoints: result.checkpoints.map((c) => ({
        tick: c.tick,
        stateHash: c.hash,
      })),
    },
  }
}

/** One key per line, each value on one line, so a diff shows which part changed. */
function serialize(golden: Golden): string {
  const lines = Object.entries(golden).map(
    ([key, value]) => `  ${JSON.stringify(key)}: ${JSON.stringify(value)}`
  )
  return `{\n${lines.join(",\n")}\n}\n`
}

function loadGoldens(): readonly [string, Golden][] {
  return readdirSync(GOLDEN_DIR)
    .filter((file) => file.endsWith(".json"))
    .sort()
    .map((file) => [
      file,
      JSON.parse(readFileSync(new URL(file, GOLDEN_DIR), "utf8")) as Golden,
    ])
}

interface Coverage {
  readonly kinds: Set<ObstacleKind>
  readonly effects: Set<TimedEffect | "retry">
  readonly events: Set<"hit" | "death" | "takeover" | "hold" | "magnet">
}

/** What a run exercises, so the set as a whole is known to reach every rule. */
function cover(run: Run, into: Coverage): void {
  const flaps = new Set<number>()
  let at = 0
  for (const delta of run.flapDeltas) flaps.add((at += delta))
  let state = initialState(run.seed)
  while (state.tick < run.endTick) {
    const next = step(state, flaps.has(state.tick))
    for (let i = state.nextPass; i < next.nextPass; i++)
      into.kinds.add(obstacle(run.seed, i).kind)
    if (next.lives < state.lives) into.events.add("hit")
    if (next.lives > state.lives) into.effects.add("retry")
    if (next.dead) into.events.add("death")
    if (
      next.effect.kind !== "none" &&
      (state.effect.kind !== next.effect.kind ||
        next.effect.remaining > state.effect.remaining)
    )
      into.effects.add(next.effect.kind)
    if (state.effect.kind === "autopilot" && next.flaps > state.flaps)
      into.events.add("takeover")
    if (
      state.effect.kind === "autopilot" &&
      state.effect.remaining === 1 &&
      next.effect.kind === "autopilot"
    )
      into.events.add("hold")
    if (next.free.length > 0) into.events.add("magnet")
    state = next
  }
}

describe("golden runs", () => {
  it.runIf(RECORD)("records every recipe", () => {
    mkdirSync(GOLDEN_DIR, { recursive: true })
    for (const [name, recipe] of Object.entries(RECIPES)) {
      const golden = goldenOf(record(recipe))
      const file = new URL(`${name}.json`, GOLDEN_DIR)
      writeFileSync(file, serialize(golden))
      expect(JSON.parse(readFileSync(file, "utf8"))).toEqual(golden)
    }
  })

  it("replay to their recorded score and checkpoints", () => {
    const goldens = loadGoldens()
    expect(goldens.map(([file]) => file)).toEqual(
      Object.keys(RECIPES)
        .map((name) => `${name}.json`)
        .sort()
    )
    for (const [file, golden] of goldens) {
      expect({ file, rulesVersion: golden.rulesVersion }).toEqual({
        file,
        rulesVersion: RULES_VERSION,
      })
      expect({ file, ...goldenOf(golden).expected }).toEqual({
        file,
        ...golden.expected,
      })
    }
  })

  it("together reach every obstacle, power-up and hand-off rule", () => {
    const coverage: Coverage = {
      kinds: new Set(),
      effects: new Set(),
      events: new Set(),
    }
    for (const [, golden] of loadGoldens()) cover(golden, coverage)
    expect([...coverage.kinds].sort()).toEqual(
      ["ci", "conflict", "pr", "terminal"].sort()
    )
    expect([...coverage.effects].sort()).toEqual(
      ["autopilot", "cacheHit", "forcePush", "retry"].sort()
    )
    expect([...coverage.events].sort()).toEqual(
      ["death", "hit", "hold", "magnet", "takeover"].sort()
    )
  })
})
