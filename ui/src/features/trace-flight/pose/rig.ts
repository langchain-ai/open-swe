/**
 * The bird's pose: a cosmetic function of what the flight is doing, stepped once
 * per game tick. It never feeds back into the game, so it can't affect a replay.
 *
 * The bird is the Robin mark. On the Ready screen it holds the logo exactly; in
 * flight its head, tail and wings blend key poses: glide → stroke → reach on each
 * flap, and the dart during Force push. Every value is sprung, and each part
 * leaves the logo at its own speed, so shape changes overlap instead of snapping.
 */
import { lerp } from "./geometry"
import { MAX_FALL_SPEED, type RigParams } from "./params"
import {
  blendPose,
  CHANNEL_JOINT,
  mapChannels,
  mapJoints,
  PERCHED,
  sidePose,
  type JointChannel,
  type JointName,
  type JointPose,
} from "./poses"
import { AT_REST, kick, stepSpring, type Spring } from "./springs"

export type FlightMode = "ready" | "play" | "forcePush" | "autopilot"

export interface RigInput {
  /** Vertical speed in world units per tick; positive is falling. */
  readonly vy: number
  readonly flapped: boolean
  readonly hit: boolean
  readonly mode: FlightMode
  /** The bird blinks while this is above zero. */
  readonly invulnerableTicks: number
}

export interface RigState {
  readonly tick: number
  readonly mode: FlightMode
  readonly lastFlapTick: number | null
  readonly beatStartTick: number | null
  readonly lastHitTick: number | null
  readonly pitch: Spring
  readonly squash: Spring
  readonly bob: Spring
  readonly sweep: Spring
  readonly dart: Spring
  readonly morph: Readonly<Record<JointName, Spring>>
  readonly joints: Readonly<Record<JointChannel, Spring>>
  readonly invulnerableTicks: number
}

/** The upper lid alone (blinks, focus), or both lids meeting in a slit (resting). */
export type Lids = "upper" | "both"

export interface BirdPose {
  /** Vertical offset in mark units (idle breathing). */
  readonly offsetY: number
  /** Whole-body rotation in degrees, positive clockwise (nose down). */
  readonly angle: number
  readonly scaleX: number
  readonly scaleY: number
  readonly alpha: number
  readonly joints: JointPose
  /** How far the eye is shut: 0 open, 1 closed. */
  readonly eye: number
  readonly lids: Lids
}

export const INITIAL_RIG: RigState = {
  tick: 0,
  mode: "ready",
  lastFlapTick: null,
  beatStartTick: null,
  lastHitTick: null,
  pitch: AT_REST,
  squash: AT_REST,
  bob: AT_REST,
  sweep: AT_REST,
  dart: AT_REST,
  morph: mapJoints(() => AT_REST),
  joints: mapChannels(() => AT_REST),
  invulnerableTicks: 0,
}

const BLINK_ALPHA = 0.35
const MAX_SQUASH = 0.5
const MAX_SWEEP = 1.3
/** Each channel's share of the hit kick: the head snaps back, the tail flares, the wings flail. */
const RUFFLE: Readonly<Record<JointChannel, number>> = {
  head: 1,
  headReach: 0,
  tail: -1,
  beat: 2,
  spread: 0,
}
/** Follow-through direction: a quick nose-down swings the beak up and the tail down. */
const LAG_SIGN: Readonly<Record<JointName, number>> = {
  head: 1,
  tail: -1,
  wings: 0,
}
const LIMITS: Readonly<Record<JointChannel, readonly [number, number]>> = {
  head: [-25, 25],
  headReach: [-10, 40],
  tail: [-30, 30],
  beat: [-90, 95],
  spread: [0, 1.2],
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function easeOut(t: number): number {
  return 1 - (1 - t) ** 3
}

function easeInOut(t: number): number {
  return t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2
}

/** Where the wings should be on this tick: the stroke, the recovery, or resting by fall speed. */
function sweepTarget(
  tick: number,
  beatStartTick: number | null,
  input: RigInput,
  params: RigParams
): number {
  const w = params.wingbeat
  const rest = -w.fallLean * clamp(input.vy / MAX_FALL_SPEED, 0, 1)
  if (beatStartTick === null) return rest
  const scale = input.mode === "autopilot" ? params.autopilotKickScale : 1
  const t = tick - beatStartTick
  if (t < w.downTicks)
    return lerp(rest, w.stroke * scale, easeOut((t + 1) / w.downTicks))
  if (t < w.downTicks + w.recoverTicks)
    return lerp(
      w.stroke * scale,
      w.recover * scale,
      easeInOut((t - w.downTicks + 1) / w.recoverTicks)
    )
  return rest
}

export function stepRig(
  state: RigState,
  input: RigInput,
  params: RigParams
): RigState {
  const tick = state.tick + 1
  // Force push holds the wings tucked, so a flap there has nothing to animate.
  const flapped = input.flapped && input.mode !== "forcePush"
  const beatStartTick = flapped
    ? tick
    : input.mode === "ready"
      ? null
      : state.beatStartTick
  const afterPause =
    state.lastFlapTick === null ||
    tick - state.lastFlapTick >= params.anticipationGapTicks
  const kickScale = input.mode === "autopilot" ? params.autopilotKickScale : 1

  const pitchTarget =
    input.mode === "ready"
      ? 0
      : input.mode === "forcePush"
        ? params.pitch.forcePushLean
        : clamp(
            input.vy * params.pitch.degreesPerSpeed,
            -params.pitch.maxUp,
            params.pitch.maxDown
          )
  const pitch = stepSpring(
    input.hit ? kick(state.pitch, params.hit.wobbleKick) : state.pitch,
    pitchTarget,
    params.pitch
  )

  const speed = Math.min(Math.abs(input.vy) / MAX_FALL_SPEED, 1)
  const handedOff = state.mode === "autopilot" && input.mode === "play"
  const squashKick =
    (flapped
      ? (params.squash.flapKick +
          (afterPause ? params.squash.anticipationKick : 0)) *
        kickScale
      : 0) + (handedOff ? params.squash.handOffKick : 0)
  const squash = stepSpring(
    kick(state.squash, squashKick),
    -params.squash.speedStretch * speed,
    params.squash
  )

  const bob = stepSpring(
    state.bob,
    input.mode === "ready"
      ? params.idle.amplitude *
          Math.sin((2 * Math.PI * tick) / params.idle.periodTicks)
      : 0,
    params.idle.tuning
  )
  const sweep = stepSpring(
    state.sweep,
    sweepTarget(tick, beatStartTick, input, params),
    params.sweep
  )
  const dart = stepSpring(
    state.dart,
    input.mode === "forcePush" ? 1 : 0,
    params.dart
  )

  const flight = blendPose(
    sidePose(clamp(sweep.value, -MAX_SWEEP, MAX_SWEEP), params.poses),
    params.poses.dart,
    clamp(dart.value, 0, 1)
  )
  const morph = mapJoints((joint) =>
    stepSpring(
      state.morph[joint],
      input.mode === "ready" ? 0 : 1,
      params.morph[joint]
    )
  )

  const joints = mapChannels((channel) => {
    const joint = CHANNEL_JOINT[channel]
    const tuning = params.joints[joint]
    const out = morph[joint].value
    // Real birds hold their head level while the body pitches.
    const level = channel === "head" ? params.headLevel * pitch.value * out : 0
    const lagging =
      channel === "headReach" || channel === "spread"
        ? 0
        : LAG_SIGN[joint] * tuning.lag * pitch.velocity
    const target =
      lerp(PERCHED[channel], flight[channel], out) + level + lagging
    const ruffle = input.hit ? params.hit.ruffleKick * RUFFLE[channel] : 0
    return stepSpring(
      kick(state.joints[channel], ruffle),
      target,
      tuning.spring
    )
  })

  return {
    tick,
    mode: input.mode,
    lastFlapTick: flapped ? tick : state.lastFlapTick,
    beatStartTick,
    lastHitTick: input.hit ? tick : state.lastHitTick,
    pitch,
    squash,
    bob,
    sweep,
    dart,
    morph,
    joints,
    invulnerableTicks: input.invulnerableTicks,
  }
}

function eyeOf(state: RigState, params: RigParams): number {
  const { eye } = params
  if (
    state.lastHitTick !== null &&
    state.tick - state.lastHitTick < eye.squintTicks
  )
    return 1
  if (state.mode === "forcePush") return eye.focus
  if (state.mode === "autopilot") return eye.cruise
  const sinceBlink =
    (state.tick % eye.blinkEveryTicks) - (eye.blinkEveryTicks - eye.blinkTicks)
  return sinceBlink >= 0
    ? Math.sin((Math.PI * (sinceBlink + 1)) / (eye.blinkTicks + 1))
    : 0
}

export function poseOf(state: RigState, params: RigParams): BirdPose {
  const scaleX = 1 + clamp(state.squash.value, -MAX_SQUASH, MAX_SQUASH)
  const halfBlink = Math.max(1, Math.floor(params.hit.blinkPeriodTicks / 2))
  const dimmed =
    state.invulnerableTicks > 0 && Math.floor(state.tick / halfBlink) % 2 === 0
  return {
    offsetY: state.bob.value,
    angle: state.pitch.value,
    scaleX,
    // Squash and stretch keep the bird's area constant.
    scaleY: 1 / scaleX,
    alpha: dimmed ? BLINK_ALPHA : 1,
    joints: mapChannels((c) =>
      clamp(state.joints[c].value, LIMITS[c][0], LIMITS[c][1])
    ),
    eye: eyeOf(state, params),
    lids: state.mode === "autopilot" ? "both" : "upper",
  }
}

/** Blends two consecutive tick poses for displays faster than the tick rate. */
export function lerpPose(a: BirdPose, b: BirdPose, t: number): BirdPose {
  return {
    offsetY: lerp(a.offsetY, b.offsetY, t),
    angle: lerp(a.angle, b.angle, t),
    scaleX: lerp(a.scaleX, b.scaleX, t),
    scaleY: lerp(a.scaleY, b.scaleY, t),
    alpha: b.alpha,
    joints: blendPose(a.joints, b.joints, t),
    eye: b.eye,
    lids: b.lids,
  }
}
