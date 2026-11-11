import {
  DEFAULT_POSES,
  type JointName,
  type JointPose,
  type KeyPoseName,
} from "./poses"
import type { SpringTuning } from "./springs"
import { DEFAULT_WING, type WingShape } from "./wings"

/** How one part follows its target. Wings are stiff; head and tail trail. */
export interface JointTuning {
  readonly spring: SpringTuning
  /** Degrees this part trails per degree-per-second of body pitch change (follow-through). */
  readonly lag: number
}

export interface WingbeatParams {
  /** Ticks for the power stroke that sweeps the wings down. */
  readonly downTicks: number
  /** Ticks to raise them again. */
  readonly recoverTicks: number
  /** Sweep at the bottom of the stroke (+1 is the stroke pose). */
  readonly stroke: number
  /** Sweep the wings swing to on the way back (−1 is the reach pose). */
  readonly recover: number
  /** How far the wings reach up at full fall speed, like a parachute. */
  readonly fallLean: number
}

export interface RigParams {
  readonly pitch: SpringTuning & {
    /** Degrees of nose-down per world unit per tick of fall speed. */
    readonly degreesPerSpeed: number
    readonly maxUp: number
    readonly maxDown: number
    /** Nose-down lean held through Force push, in degrees. */
    readonly forcePushLean: number
  }
  readonly squash: SpringTuning & {
    /** Squash velocity added on a flap; 0.1 squash is 10% wider and 9% shorter. */
    readonly flapKick: number
    /** Extra squash on a flap after a pause, so the first flap reads as a crouch. */
    readonly anticipationKick: number
    /** Stretch at full fall speed. */
    readonly speedStretch: number
    /** Bounce when Autopilot hands control back. */
    readonly handOffKick: number
  }
  readonly wingbeat: WingbeatParams
  /** Springs on the blend weights: wing sweep and the Force push dart. */
  readonly sweep: SpringTuning
  readonly dart: SpringTuning
  readonly poses: Readonly<Record<KeyPoseName, JointPose>>
  /** How fast each part travels between the logo and flight; different speeds overlap the change. */
  readonly morph: Readonly<Record<JointName, SpringTuning>>
  readonly joints: Readonly<Record<JointName, JointTuning>>
  /** Share of body pitch the head turns back against, so it stays level like a real bird's. */
  readonly headLevel: number
  readonly wing: WingShape
  /** A flap counts as "after a pause" when the previous one was this many ticks ago. */
  readonly anticipationGapTicks: number
  /** Breathing hover on the Ready screen, in mark units. */
  readonly idle: {
    readonly amplitude: number
    readonly periodTicks: number
    readonly tuning: SpringTuning
  }
  readonly hit: {
    readonly wobbleKick: number
    /** Kick to the head, tail and wings, in degrees per second, so a hit ruffles the bird. */
    readonly ruffleKick: number
    readonly blinkPeriodTicks: number
  }
  readonly eye: {
    /** A blink every this many ticks. */
    readonly blinkEveryTicks: number
    readonly blinkTicks: number
    /** Eye squeezed shut after a hit. */
    readonly squintTicks: number
    /** How far the eye narrows during Force push. */
    readonly focus: number
    /** How much of the eye both lids cover while Autopilot flies, leaving a slit. */
    readonly cruise: number
  }
  /** Autopilot flaps are calmer than the player's. */
  readonly autopilotKickScale: number
}

/** Fastest fall in world units per tick (plan §3.2: 3584 Q8). */
export const MAX_FALL_SPEED = 14

/**
 * Starting values. The stroke (about 5 frames) and recovery (about 10) are
 * measured from the LangSmith launch video at 0:52; everything is tuned in the lab.
 */
export const DEFAULT_PARAMS: RigParams = {
  pitch: {
    frequency: 3,
    damping: 0.55,
    degreesPerSpeed: 2.4,
    maxUp: 25,
    maxDown: 40,
    forcePushLean: 22,
  },
  squash: {
    frequency: 5,
    damping: 0.35,
    flapKick: 2.5,
    anticipationKick: 1.2,
    speedStretch: 0.08,
    handOffKick: 1.2,
  },
  wingbeat: {
    downTicks: 5,
    recoverTicks: 10,
    stroke: 1,
    recover: -0.6,
    fallLean: 0.5,
  },
  sweep: { frequency: 7, damping: 0.55 },
  dart: { frequency: 4, damping: 0.7 },
  poses: DEFAULT_POSES,
  morph: {
    head: { frequency: 3.2, damping: 0.5 },
    tail: { frequency: 2.6, damping: 0.5 },
    wings: { frequency: 3.6, damping: 0.55 },
  },
  joints: {
    head: { spring: { frequency: 6, damping: 0.5 }, lag: 0.04 },
    tail: { spring: { frequency: 4.5, damping: 0.45 }, lag: 0.1 },
    wings: { spring: { frequency: 10, damping: 0.55 }, lag: 0 },
  },
  headLevel: 0.45,
  wing: DEFAULT_WING,
  anticipationGapTicks: 15,
  idle: {
    amplitude: 20,
    periodTicks: 150,
    tuning: { frequency: 2, damping: 0.8 },
  },
  hit: { wobbleKick: 300, ruffleKick: 400, blinkPeriodTicks: 8 },
  eye: {
    blinkEveryTicks: 210,
    blinkTicks: 7,
    squintTicks: 30,
    focus: 0.35,
    cruise: 0.7,
  },
  autopilotKickScale: 0.7,
}
