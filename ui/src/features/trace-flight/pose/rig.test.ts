import { describe, expect, it } from "vitest"

import { DEFAULT_PARAMS } from "./params"
import { DEFAULT_POSES, JOINT_CHANNELS, PERCHED } from "./poses"
import {
  INITIAL_RIG,
  lerpPose,
  poseOf,
  stepRig,
  type RigInput,
  type RigState,
} from "./rig"

const PLAY: RigInput = {
  vy: 0,
  flapped: false,
  hit: false,
  mode: "play",
  invulnerableTicks: 0,
}
const READY: RigInput = { ...PLAY, mode: "ready" }
const FLAP: RigInput = { ...PLAY, flapped: true }

function run(inputs: readonly RigInput[], from: RigState = INITIAL_RIG) {
  return inputs.reduce((s, input) => stepRig(s, input, DEFAULT_PARAMS), from)
}

function repeat(input: RigInput, ticks: number): RigInput[] {
  return Array.from({ length: ticks }, () => input)
}

/** A bird already out of the logo and gliding side-on. */
const FLYING = run([FLAP, ...repeat(PLAY, 240)])

describe("stepRig", () => {
  it("is deterministic and leaves its input untouched", () => {
    const inputs = [FLAP, ...repeat({ ...PLAY, vy: 4 }, 30), FLAP]
    const frozen = structuredClone(INITIAL_RIG)
    expect(run(inputs)).toEqual(run(inputs))
    expect(INITIAL_RIG).toEqual(frozen)
  })

  it("holds the logo on the Ready screen, and lands back into it from flight", () => {
    const held = poseOf(run(repeat(READY, 120)), DEFAULT_PARAMS).joints
    const landed = poseOf(
      run(repeat(READY, 240), FLYING),
      DEFAULT_PARAMS
    ).joints
    for (const channel of JOINT_CHANNELS) {
      expect(held[channel]).toBeCloseTo(PERCHED[channel], 6)
      expect(landed[channel]).toBeCloseTo(PERCHED[channel], 1)
    }
  })

  it("leaves the logo for gliding flight after the first flap", () => {
    const { joints } = poseOf(FLYING, DEFAULT_PARAMS)
    expect(joints.spread).toBeCloseTo(DEFAULT_POSES.glide.spread, 2)
    expect(joints.beat).toBeCloseTo(DEFAULT_POSES.glide.beat, 0)
  })

  it("sweeps the wings down on the stroke, then up on the recovery", () => {
    const { downTicks, recoverTicks } = DEFAULT_PARAMS.wingbeat
    let s = stepRig(FLYING, FLAP, DEFAULT_PARAMS)
    let peak = s.sweep.value
    for (let i = 0; i < downTicks + 2; i++) {
      s = stepRig(s, PLAY, DEFAULT_PARAMS)
      peak = Math.max(peak, s.sweep.value)
    }
    let trough = s.sweep.value
    for (let i = 0; i < recoverTicks; i++) {
      s = stepRig(s, PLAY, DEFAULT_PARAMS)
      trough = Math.min(trough, s.sweep.value)
    }
    expect(peak).toBeGreaterThan(0.6)
    expect(trough).toBeLessThan(-0.2)
  })

  it("raises the wings when falling", () => {
    const falling = run(repeat({ ...PLAY, vy: 14 }, 120), FLYING)
    expect(falling.sweep.value).toBeLessThan(-0.3)
  })

  it("keeps the head nearly level while the body pitches", () => {
    const fall = repeat({ ...PLAY, vy: 8 }, 120)
    const stiff = { ...DEFAULT_PARAMS, headLevel: 0 }
    const level = poseOf(run(fall, FLYING), DEFAULT_PARAMS)
    const fixed = poseOf(
      fall.reduce((s, input) => stepRig(s, input, stiff), FLYING),
      stiff
    )
    expect(level.angle).toBeGreaterThan(10)
    expect(level.joints.head - fixed.joints.head).toBeCloseTo(
      DEFAULT_PARAMS.headLevel * level.angle,
      0
    )
  })

  it("leans into the dart during Force push and ignores flaps", () => {
    const pushed = run(repeat({ ...FLAP, mode: "forcePush" }, 120), FLYING)
    const { joints, angle } = poseOf(pushed, DEFAULT_PARAMS)
    expect(pushed.dart.value).toBeCloseTo(1, 1)
    expect(angle).toBeCloseTo(DEFAULT_PARAMS.pitch.forcePushLean, 0)
    expect(pushed.lastFlapTick).toBe(FLYING.lastFlapTick)
    expect(joints.headReach).toBeCloseTo(DEFAULT_POSES.dart.headReach, 0)
    expect(joints.spread).toBeCloseTo(DEFAULT_POSES.dart.spread, 1)
  })

  it("moves each part out of the logo at its own speed, so the change overlaps", () => {
    const early = run([FLAP, ...repeat(PLAY, 4)])
    const values = Object.values(early.morph).map((m) => m.value.toFixed(3))
    expect(new Set(values).size).toBeGreaterThan(1)
  })

  it("ruffles the head, tail and wings on a hit", () => {
    const hit = stepRig(FLYING, { ...PLAY, hit: true }, DEFAULT_PARAMS)
    const calm = stepRig(FLYING, PLAY, DEFAULT_PARAMS)
    for (const channel of ["head", "tail", "beat"] as const)
      expect(hit.joints[channel].velocity).not.toBeCloseTo(
        calm.joints[channel].velocity,
        1
      )
    expect(hit.pitch.velocity).toBeGreaterThan(calm.pitch.velocity)
  })
})

describe("poseOf", () => {
  it("keeps the bird's area constant", () => {
    const pose = poseOf(run([FLAP, PLAY]), DEFAULT_PARAMS)
    expect(pose.scaleX).not.toBe(1)
    expect(pose.scaleX * pose.scaleY).toBeCloseTo(1, 10)
  })

  it("blinks while invulnerable", () => {
    const alphas = new Set(
      Array.from(
        { length: DEFAULT_PARAMS.hit.blinkPeriodTicks },
        (_, i) =>
          poseOf(
            run(repeat({ ...PLAY, invulnerableTicks: 60 }, i + 1)),
            DEFAULT_PARAMS
          ).alpha
      )
    )
    expect(alphas.size).toBe(2)
  })

  it("squeezes the eye shut after a hit, then opens it", () => {
    const hit = stepRig(FLYING, { ...PLAY, hit: true }, DEFAULT_PARAMS)
    expect(poseOf(hit, DEFAULT_PARAMS).eye).toBe(1)
    const later = run(repeat(PLAY, DEFAULT_PARAMS.eye.squintTicks), hit)
    expect(poseOf(later, DEFAULT_PARAMS).eye).toBeLessThan(1)
  })
})

describe("lerpPose", () => {
  it("blends halfway between two poses", () => {
    const a = poseOf(INITIAL_RIG, DEFAULT_PARAMS)
    const b = { ...a, angle: 20, offsetY: 4 }
    const mid = lerpPose(a, b, 0.5)
    expect(mid.angle).toBe(10)
    expect(mid.offsetY).toBe(2)
  })
})
