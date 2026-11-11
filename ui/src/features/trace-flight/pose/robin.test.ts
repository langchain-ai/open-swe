import { describe, expect, it } from "vitest"

import { apply, type Point } from "./geometry"
import { MARK, type Segment } from "./mark"
import { PERCHED } from "./poses"
import { bendMark, EYE, headAffine, tailAffine } from "./robin"
import { DEFAULT_WING, outset, wingOutline } from "./wings"

function points(segments: readonly Segment[]): readonly Point[] {
  return segments.flatMap((s) =>
    s.kind === "move" ? [s.to] : s.kind === "curve" ? [s.c1, s.c2, s.to] : []
  )
}

/** Each of the mark's points paired with where a pose moves it. */
function moved(pose: typeof PERCHED) {
  const after = points(bendMark(pose))
  return points(MARK).map((before, i) => ({ before, after: after[i] }))
}

describe("bendMark", () => {
  it("draws the logo exactly at rest", () => {
    for (const { before, after } of moved(PERCHED)) {
      expect(after?.x).toBeCloseTo(before.x, 9)
      expect(after?.y).toBeCloseTo(before.y, 9)
    }
  })

  // The eyelid is drawn with the head's rigid transform, so the eye must not bend.
  it("carries the eye rigidly with the head", () => {
    const pose = { ...PERCHED, head: 15, headReach: 20 }
    const head = headAffine(pose)
    const eye = moved(pose).filter(
      ({ before }) =>
        Math.hypot(before.x - EYE.x, before.y - EYE.y) <= EYE.radius + 1
    )
    expect(eye.length).toBeGreaterThan(10)
    for (const { before, after } of eye) {
      const rigid = apply(head, before)
      expect(after?.x).toBeCloseTo(rigid.x, 6)
      expect(after?.y).toBeCloseTo(rigid.y, 6)
    }
  })

  it("swings the tail tip with the tail and leaves the belly still", () => {
    const pose = { ...PERCHED, tail: 15 }
    const all = moved(pose)
    const tip = all.reduce((a, b) => (b.before.x < a.before.x ? b : a))
    const belly = all.reduce((a, b) => (b.before.y > a.before.y ? b : a))
    const swung = apply(tailAffine(pose), tip.before)
    expect(tip.after?.x).toBeCloseTo(swung.x, 6)
    expect(tip.after?.y).toBeCloseTo(swung.y, 6)
    expect(belly.after).toEqual(belly.before)
  })
})

describe("outset", () => {
  it("grows an outline outward whichever way it winds", () => {
    const square = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
      { x: 0, y: 10 },
    ]
    for (const outline of [square, [...square].reverse()]) {
      const grown = outset(outline, 2)
      grown.forEach((p, i) => {
        const before = outline[i] ?? p
        expect(Math.hypot(p.x - 5, p.y - 5)).toBeGreaterThan(
          Math.hypot(before.x - 5, before.y - 5)
        )
      })
    }
  })

  // The wingbeat passes 0° on every flap, where the wing is seen edge-on.
  it("stays finite for an edge-on wing", () => {
    for (const feathers of [1, 1.05]) {
      const edgeOn = wingOutline(0, 1, { ...DEFAULT_WING, feathers }, "near")
      for (const p of outset(edgeOn, DEFAULT_WING.gap)) {
        expect(Number.isFinite(p.x)).toBe(true)
        expect(Number.isFinite(p.y)).toBe(true)
      }
    }
  })
})
