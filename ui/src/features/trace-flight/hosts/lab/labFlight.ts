/**
 * Lab-only stand-in for the game's flight so the bird has real motion to react
 * to: the game's own speeds and timers, with no course.
 */
import {
  BIRD_RADIUS,
  DURATION,
  FLAP_VY_Q8,
  FLOOR,
  GRAVITY_Q8,
  INVULNERABLE_TICKS,
  MAX_FALL_Q8,
  Q,
  START_Y,
} from "../../core/rules"
import type { FlightMode, RigInput } from "../../pose/rig"

const GRAVITY = GRAVITY_Q8 / Q
const FLAP_SPEED = FLAP_VY_Q8 / Q
const MAX_FALL = MAX_FALL_Q8 / Q
/** Ticks resting on the floor before the lab puts the bird back on Ready. */
const FLOOR_REST_TICKS = 45

/** "land" puts the bird back in the logo, as the game does on exit. */
export type LabCommand = "flap" | "hit" | "forcePush" | "autopilot" | "land"

export interface LabFlight {
  readonly y: number
  readonly vy: number
  readonly mode: FlightMode
  readonly modeTicks: number
  readonly invulnerableTicks: number
  readonly floorTicks: number
}

export const READY: LabFlight = {
  y: START_Y,
  vy: 0,
  mode: "ready",
  modeTicks: 0,
  invulnerableTicks: 0,
  floorTicks: 0,
}

/** One tick: apply commands, move, and describe the tick for the pose rig. */
export function stepFlight(
  flight: LabFlight,
  commands: readonly LabCommand[]
): { readonly flight: LabFlight; readonly rig: RigInput } {
  const has = (c: LabCommand) => commands.includes(c)
  let { y, vy, mode, modeTicks, invulnerableTicks, floorTicks } = flight

  if (has("land")) [mode, modeTicks] = ["ready", 0]
  if (has("forcePush")) [mode, modeTicks] = ["forcePush", DURATION.forcePush]
  if (has("autopilot")) [mode, modeTicks] = ["autopilot", DURATION.autopilot]
  const hit = has("hit") && invulnerableTicks === 0
  if (hit) invulnerableTicks = INVULNERABLE_TICKS

  const bot = mode === "autopilot" && y > START_Y + 20 && vy >= 0
  const flapped = bot || (has("flap") && (mode === "play" || mode === "ready"))
  if (flapped) {
    if (mode === "ready") mode = "play"
    vy = FLAP_SPEED
  }

  if (mode === "ready") {
    y += (START_Y - y) / 10
    vy = 0
  } else if (mode === "forcePush") {
    vy = 0
  } else {
    vy = Math.min(vy + GRAVITY, MAX_FALL)
    y += vy
  }

  if (y < BIRD_RADIUS) [y, vy] = [BIRD_RADIUS, 0]
  if (y > FLOOR - BIRD_RADIUS) [y, vy] = [FLOOR - BIRD_RADIUS, 0]
  floorTicks = y >= FLOOR - BIRD_RADIUS ? floorTicks + 1 : 0

  if (modeTicks > 0 && --modeTicks === 0) mode = "play"
  if (invulnerableTicks > 0) invulnerableTicks -= 1

  const next =
    floorTicks > FLOOR_REST_TICKS
      ? { ...READY, y }
      : { y, vy, mode, modeTicks, invulnerableTicks, floorTicks }
  return {
    flight: next,
    rig: {
      vy: next.vy,
      flapped,
      hit,
      mode: next.mode,
      invulnerableTicks: next.invulnerableTicks,
    },
  }
}
