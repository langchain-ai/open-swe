/**
 * Key poses: where the head, tail and wings sit, which the rig blends between.
 * The bird is always side-on and faces right, as the Robin mark does.
 */
import { lerp } from "./geometry"

export interface JointPose {
  /** Head turn in degrees; positive lifts the beak. */
  readonly head: number
  /** How far the head pushes forward along the beak, in mark units. */
  readonly headReach: number
  /** Tail turn in degrees; positive lifts the tail. */
  readonly tail: number
  /** Wingbeat in degrees: positive raises the wings above the back, negative sweeps them down. */
  readonly beat: number
  /** 0 keeps the wings folded into the body (the logo); 1 opens them fully. */
  readonly spread: number
}

export type JointChannel = keyof JointPose

export const JOINT_CHANNELS: readonly JointChannel[] = [
  "head",
  "headReach",
  "tail",
  "beat",
  "spread",
]

/** Parts that leave the logo and spring together. */
export type JointName = "head" | "tail" | "wings"

export const JOINT_NAMES: readonly JointName[] = ["head", "tail", "wings"]

export const CHANNEL_JOINT: Readonly<Record<JointChannel, JointName>> = {
  head: "head",
  headReach: "head",
  tail: "tail",
  beat: "wings",
  spread: "wings",
}

export type KeyPoseName = "glide" | "stroke" | "reach" | "dart"

export const KEY_POSE_NAMES: readonly KeyPoseName[] = [
  "glide",
  "stroke",
  "reach",
  "dart",
]

/** The logo exactly. */
export const PERCHED: JointPose = {
  head: 0,
  headReach: 0,
  tail: 0,
  beat: 0,
  spread: 0,
}

export function mapChannels<T>(
  f: (channel: JointChannel) => T
): Record<JointChannel, T> {
  return {
    head: f("head"),
    headReach: f("headReach"),
    tail: f("tail"),
    beat: f("beat"),
    spread: f("spread"),
  }
}

export function mapJoints<T>(f: (joint: JointName) => T): Record<JointName, T> {
  return { head: f("head"), tail: f("tail"), wings: f("wings") }
}

export function blendPose(a: JointPose, b: JointPose, t: number): JointPose {
  return mapChannels((c) => lerp(a[c], b[c], t))
}

export const DEFAULT_POSES: Readonly<Record<KeyPoseName, JointPose>> = {
  glide: { head: 0, headReach: 0, tail: 0, beat: 18, spread: 1 },
  // Bottom of the power stroke: wings swept down, tail kicks up.
  stroke: { head: -3, headReach: 0, tail: 10, beat: -60, spread: 1 },
  // Top of the recovery: wings raised over the back, tail dips.
  reach: { head: 4, headReach: 0, tail: -8, beat: 80, spread: 1 },
  // Force push: wings tucked back along the body, head pushed forward.
  dart: { head: -6, headReach: 26, tail: -6, beat: 8, spread: 0.45 },
}

/** At a wing sweep: −1 reaching up, 0 gliding, +1 bottom of the stroke. */
export function sidePose(
  sweep: number,
  poses: Readonly<Record<KeyPoseName, JointPose>>
): JointPose {
  return sweep >= 0
    ? blendPose(poses.glide, poses.stroke, sweep)
    : blendPose(poses.glide, poses.reach, -sweep)
}
