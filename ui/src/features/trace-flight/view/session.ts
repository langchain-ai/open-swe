/**
 * The imperative shell around one run: the sim, the bird's pose rig, the input
 * log and what the view needs to remember between ticks. The sim and the rig
 * stay pure; only this object changes.
 */
import { botWantsFlap } from "../core/bot"
import { obstacle, pickupY, type PickupKind } from "../core/course"
import { toDeltas, type EndReason, type Run } from "../core/replay"
import {
  DEFAULT_TUNING,
  FLAP_VY_Q8,
  GRAVITY_Q8,
  Q,
  TICK_CAP,
  type Tuning,
} from "../core/rules"
import { modeOf, step } from "../core/sim"
import { initialState, type SimState } from "../core/state"
import { DEFAULT_PARAMS } from "../pose/params"
import {
  INITIAL_RIG,
  poseOf,
  stepRig,
  type BirdPose,
  type RigInput,
  type RigState,
} from "../pose/rig"

/** A pickup the bird collected: where it was in the course, and when. */
export interface Collected {
  readonly kind: PickupKind
  readonly x: number
  readonly y: number
  readonly tick: number
}

export interface Session {
  readonly seed: number
  /** Null on the Ready screen: the run starts with the first flap. */
  sim: SimState | null
  prev: SimState | null
  rig: RigState
  prevPose: BirdPose
  pose: BirdPose
  /** Ticks of accepted flaps, the run's input log. */
  readonly flapTicks: number[]
  /** A flap waiting for the next tick; one per tick at most. */
  queued: boolean
  ended: EndReason | null
  /** Seconds of real time not yet stepped. */
  accumulator: number
  /** Flap ticks from a shared link, replayed as the run reaches them. */
  readonly scripted: ReadonlySet<number>
  readonly collected: Collected[]
}

export interface SessionControls {
  /** The scripted player flies instead of the person (harness only). */
  readonly bot: boolean
  readonly tuning: Tuning
}

export const PLAYER: SessionControls = { bot: false, tuning: DEFAULT_TUNING }

const READY: RigInput = {
  vy: 0,
  flapped: false,
  hit: false,
  mode: "ready",
  invulnerableTicks: 0,
}

export function newSession(
  seed: number,
  scripted: ReadonlySet<number> = new Set()
): Session {
  const pose = poseOf(INITIAL_RIG, DEFAULT_PARAMS)
  return {
    seed: seed >>> 0,
    sim: null,
    prev: null,
    rig: INITIAL_RIG,
    prevPose: pose,
    pose,
    flapTicks: [],
    queued: false,
    ended: null,
    accumulator: 0,
    scripted,
    collected: [],
  }
}

function stepPose(session: Session, input: RigInput): void {
  session.rig = stepRig(session.rig, input, DEFAULT_PARAMS)
  session.prevPose = session.pose
  session.pose = poseOf(session.rig, DEFAULT_PARAMS)
}

/** Pickups gone between two consecutive states, at the positions they had. */
function collectedBetween(
  prev: SimState,
  next: SimState
): readonly Collected[] {
  const before = new Set(prev.collected)
  return next.collected
    .filter((index) => !before.has(index))
    .flatMap((index) => {
      const item = obstacle(prev.seed, index)
      const pickup = item.pickup
      if (!pickup) return []
      const free = prev.free.find((p) => p.obstacle === index)
      return [
        {
          kind: pickup.kind,
          x: free ? free.x : pickup.x,
          y: free ? free.y : pickupY(item, pickup, prev.tick),
          tick: next.tick,
        },
      ]
    })
}

/** One tick of the run, and of the bird's pose. */
export function advance(session: Session, controls: SessionControls): void {
  if (session.ended) return
  if (!session.sim) {
    const starts = session.queued || controls.bot || session.scripted.has(0)
    session.queued = false
    if (!starts) return stepPose(session, READY)
    session.sim = initialState(session.seed)
  }
  const sim = session.sim
  const wants =
    sim.tick === 0 ||
    session.queued ||
    session.scripted.has(sim.tick) ||
    (controls.bot && botWantsFlap(sim, controls.tuning))
  session.queued = false
  const next = step(sim, wants, controls.tuning)
  if (next.flaps > sim.flaps) session.flapTicks.push(sim.tick)
  session.collected.push(...collectedBetween(sim, next))
  session.prev = sim
  session.sim = next
  stepPose(session, {
    vy: next.vy / Q,
    // Only a flap sets vy to the flap speed plus one tick of gravity.
    flapped: next.vy === FLAP_VY_Q8 + GRAVITY_Q8,
    hit: next.lives < sim.lives,
    mode: modeOf(next.effect),
    invulnerableTicks: next.invulnerable,
  })
  if (next.dead) session.ended = "death"
  else if (next.tick >= TICK_CAP) session.ended = "cap"
}

/** Ends a run the player walked away from; a run that never started stays unsubmitted. */
export function quit(session: Session): void {
  if (session.sim && !session.ended) session.ended = "quit"
}

export function runOf(session: Session): Run | null {
  if (!session.sim) return null
  return {
    seed: session.seed,
    endTick: session.sim.tick,
    endReason: session.ended ?? "quit",
    flapDeltas: toDeltas(session.flapTicks),
  }
}
