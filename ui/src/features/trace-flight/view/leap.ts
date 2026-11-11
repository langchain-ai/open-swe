/**
 * The bird's leap out of the logo: it crouches, springs up on a high arc with
 * three wingbeats from the game's own rig, overshoots its start spot and settles.
 * Pure: the same fraction of the leap always gives the same frame.
 */
import { DEFAULT_PARAMS } from "../pose/params"
import {
  INITIAL_RIG,
  lerpPose,
  poseOf,
  stepRig,
  type BirdPose,
  type RigState,
} from "../pose/rig"
import { TICKS_PER_SECOND } from "../pose/springs"

export const LEAP_MS = 1100
const TICKS = Math.round((LEAP_MS / 1000) * TICKS_PER_SECOND)
/** The share of the leap spent crouching in the logo. */
const CROUCH = 0.12
/** World units the arc rises above the straight line from the logo. */
const LIFT = 220
/** How far past its start spot the bird carries before settling (easeOutBack). */
const OVERSHOOT = 1.6
const FLAPS: ReadonlySet<number> = new Set([8, 20, 32])

export interface LeapFrame {
  /** 0 at the logo, 1 at the start spot; above 1 while overshooting. */
  readonly along: number
  /** World units above the straight line. */
  readonly lift: number
  readonly pose: BirdPose
}

function easeOutBack(u: number): number {
  const c = OVERSHOOT
  return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2
}

function pathAt(t: number): { along: number; lift: number; crouch: number } {
  if (t < CROUCH)
    return { along: 0, lift: 0, crouch: Math.sin((Math.PI / 2) * (t / CROUCH)) }
  const u = Math.min(1, (t - CROUCH) / (1 - CROUCH))
  return {
    along: easeOutBack(u),
    lift: LIFT * Math.sin(Math.PI * u),
    crouch: 0,
  }
}

const REST = poseOf(INITIAL_RIG, DEFAULT_PARAMS)

/** The rig's pose for each tick of the leap, flapping on cue and pitching with the climb. */
const POSES: readonly BirdPose[] = (() => {
  const poses: BirdPose[] = []
  let rig: RigState = INITIAL_RIG
  for (let tick = 0; tick <= TICKS; tick++) {
    const now = pathAt(tick / TICKS)
    const next = pathAt((tick + 1) / TICKS)
    rig = stepRig(
      rig,
      {
        vy: now.lift - next.lift,
        flapped: FLAPS.has(tick),
        hit: false,
        mode: now.crouch > 0 ? "ready" : "play",
        invulnerableTicks: 0,
      },
      DEFAULT_PARAMS
    )
    const pose = poseOf(rig, DEFAULT_PARAMS)
    poses.push(
      now.crouch > 0
        ? {
            ...pose,
            scaleX: pose.scaleX * (1 + 0.18 * now.crouch),
            scaleY: pose.scaleY * (1 - 0.22 * now.crouch),
          }
        : pose
    )
  }
  return poses
})()

/** The leap at fraction `t` of its duration, 0 to 1. */
export function leapAt(t: number): LeapFrame {
  const clamped = Math.min(1, Math.max(0, t))
  const { along, lift } = pathAt(clamped)
  const at = clamped * TICKS
  const i = Math.min(TICKS - 1, Math.floor(at))
  const from = POSES[i] ?? REST
  return { along, lift, pose: lerpPose(from, POSES[i + 1] ?? from, at - i) }
}
