/**
 * Bufo cameos: about one per level, sitting in the open air after an obstacle.
 * Half the time a Bufo reacts to what's there (on fire beside a failing CI
 * check, reviewing a merge conflict, shipping it by a Force push); otherwise
 * it's a random one. Cosmetic only, but a pure function of the seed, so every
 * player meets the same Bufos in the same places.
 */
import { hash32 } from "../core/arith"
import { obstacle, type Obstacle } from "../core/course"
import { OBSTACLES_PER_LEVEL } from "../core/rules"
import clap from "../assets/bufo/clap.png"
import codeFreeze from "../assets/bufo/code-freeze.png"
import hackerman from "../assets/bufo/hackerman.png"
import keyboard from "../assets/bufo/keyboard.png"
import lgtm from "../assets/bufo/lgtm.png"
import onFire from "../assets/bufo/on-fire.png"
import review from "../assets/bufo/review.png"
import salute from "../assets/bufo/salute.png"
import shipit from "../assets/bufo/shipit.png"
import sipsCoffee from "../assets/bufo/sips-coffee.png"
import thinking from "../assets/bufo/thinking.png"
import thumbsUp from "../assets/bufo/thumbs-up.png"
import wave from "../assets/bufo/wave.png"

export interface BufoSheet {
  readonly url: string
  /** One frame's size; frames sit side by side. */
  readonly width: number
  readonly height: number
  /** Ticks each frame shows; empty for a still. */
  readonly ticks: readonly number[]
}

const repeat = (ticks: number, frames: number) =>
  Array.from({ length: frames }, () => ticks)

export const BUFOS = {
  wave: { url: wave, width: 128, height: 128, ticks: repeat(2, 4) },
  lgtm: { url: lgtm, width: 128, height: 128, ticks: [] },
  shipit: { url: shipit, width: 75, height: 75, ticks: [] },
  onFire: { url: onFire, width: 112, height: 112, ticks: [] },
  hackerman: { url: hackerman, width: 112, height: 112, ticks: repeat(4, 9) },
  keyboard: { url: keyboard, width: 112, height: 112, ticks: repeat(4, 9) },
  thinking: { url: thinking, width: 125, height: 125, ticks: [] },
  sipsCoffee: {
    url: sipsCoffee,
    width: 112,
    height: 112,
    ticks: [12, 3, 3, 3, 3, 3, 3, 3, 3, 18, 18, 3, 3, 3, 3, 3, 3, 3, 3, 12],
  },
  codeFreeze: { url: codeFreeze, width: 128, height: 128, ticks: [] },
  review: { url: review, width: 96, height: 91, ticks: [] },
  clap: { url: clap, width: 81, height: 71, ticks: repeat(6, 6) },
  thumbsUp: { url: thumbsUp, width: 128, height: 128, ticks: [] },
  salute: { url: salute, width: 112, height: 112, ticks: [] },
} as const satisfies Record<string, BufoSheet>

export type BufoName = keyof typeof BUFOS

const EVERYONE = Object.keys(BUFOS) as BufoName[]

/** Who shows up beside what; a Bufo reacts to a pickup before an obstacle type. */
const REACTS: Readonly<Record<string, readonly BufoName[]>> = {
  ci: ["onFire", "codeFreeze"],
  conflict: ["review", "lgtm"],
  forcePush: ["shipit"],
  cacheHit: ["sipsCoffee"],
  autopilot: ["keyboard", "hackerman"],
  retry: ["thumbsUp", "clap"],
}

/** Kept apart from the rules' salts: cameos never touch the sim. */
const SALT = 0xb0f0

export interface Cameo {
  /** The obstacle the Bufo sits after. */
  readonly index: number
  readonly bufo: BufoName
}

function reactionTo(item: Obstacle): readonly BufoName[] | undefined {
  const pickup = item.pickup?.kind
  return (pickup && REACTS[pickup]) ?? REACTS[item.kind]
}

function firstPick(seed: number, level: number): Cameo {
  const first = (level - 1) * OBSTACLES_PER_LEVEL
  if (level === 1) return { index: 1, bufo: "wave" }
  // Slots 2–8 keep the Bufo off the level's edges.
  const start = hash32(seed, level, SALT) % 7
  if (hash32(seed, level, SALT + 1) % 2 === 0)
    for (let k = 0; k < 7; k++) {
      const index = first + 2 + ((start + k) % 7)
      const options = reactionTo(obstacle(seed, index))
      if (options)
        return {
          index,
          bufo: options[hash32(seed, index, SALT + 2) % options.length]!,
        }
    }
  return {
    index: first + 2 + start,
    bufo: EVERYONE[hash32(seed, level, SALT + 3) % EVERYONE.length]!,
  }
}

/** Levels resolved so far for the current seed; each depends on the one before. */
let resolved: { seed: number; cameos: Cameo[] } = { seed: -1, cameos: [] }

/** The level's Bufo; never the same one two levels running. */
export function cameoOf(seed: number, level: number): Cameo {
  if (resolved.seed !== seed) resolved = { seed, cameos: [] }
  const { cameos } = resolved
  while (cameos.length < level) {
    const pick = firstPick(seed, cameos.length + 1)
    const before = cameos.at(-1)
    const next = (EVERYONE.indexOf(pick.bufo) + 1) % EVERYONE.length
    cameos.push(
      before?.bufo === pick.bufo
        ? { index: pick.index, bufo: EVERYONE[next]! }
        : pick
    )
  }
  return cameos[level - 1]!
}

/** The frame a sheet shows on a tick, looping. */
export function frameAt(sheet: BufoSheet, tick: number): number {
  const total = sheet.ticks.reduce((a, b) => a + b, 0)
  if (total === 0) return 0
  let t = tick % total
  for (const [i, ticks] of sheet.ticks.entries()) {
    if (t < ticks) return i
    t -= ticks
  }
  return 0
}
