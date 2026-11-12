/**
 * How the Robin mark bends. The mark is one traced outline with no separate
 * parts, so the head and tail move by carrying the mark's own points with them:
 * each point follows a part by a weight that ramps smoothly across a band at the
 * joint. Joints flex instead of tearing, and at rest every point stays put.
 */
import {
  apply,
  rotationAbout,
  smoothstep,
  type Affine,
  type Point,
} from "./geometry"
import { MARK, mapSegment, type Segment } from "./mark"
import type { JointPose } from "./poses"

/** Landmarks in mark units, measured from the traced path. */
export const EYE = { x: 709.5, y: 317.5, radius: 26.5 } as const
export const HEAD_PIVOT: Point = { x: 765, y: 400 }
export const TAIL_PIVOT: Point = { x: 318, y: 660 }
/** Toward the beak tip. */
const HEAD_FORWARD: Point = { x: 0.98, y: -0.19 }

/** A joint's blend band: a line through `origin`, with `normal` pointing into the moving part. */
export interface Band {
  readonly origin: Point
  readonly normal: Point
  /** Distance either side of the line over which points blend from body to part. */
  readonly halfWidth: number
}

// Keep the whole eye more than halfWidth above this line: the eyelid is drawn
// with the head's rigid transform, so a bent eye would drift out from under it.
export const NECK: Band = {
  origin: { x: 769, y: 456 },
  normal: { x: 0.536, y: -0.844 },
  halfWidth: 55,
}

export const TAIL_ROOT: Band = {
  origin: TAIL_PIVOT,
  normal: { x: -0.953, y: 0.303 },
  halfWidth: 30,
}

/** 0 on the body side of the band, 1 on the part's side. */
function weightAcross(band: Band, p: Point): number {
  const distance =
    (p.x - band.origin.x) * band.normal.x +
    (p.y - band.origin.y) * band.normal.y
  return smoothstep(-band.halfWidth, band.halfWidth, distance)
}

export function headAffine(pose: JointPose): Affine {
  // Canvas angles turn clockwise, which drops the beak; head is positive beak-up.
  return rotationAbout(HEAD_PIVOT, -pose.head, {
    x: pose.headReach * HEAD_FORWARD.x,
    y: pose.headReach * HEAD_FORWARD.y,
  })
}

export function tailAffine(pose: JointPose): Affine {
  // The tail sits left of its pivot, so turning clockwise lifts it.
  return rotationAbout(TAIL_PIVOT, pose.tail)
}

/** The mark with its head and tail moved. At rest it is the mark itself. */
export function bendMark(pose: JointPose): readonly Segment[] {
  const head = headAffine(pose)
  const tail = tailAffine(pose)
  const bend = (p: Point): Point => {
    const h = apply(head, p)
    const t = apply(tail, p)
    const wh = weightAcross(NECK, p)
    const wt = weightAcross(TAIL_ROOT, p)
    return {
      x: p.x + wh * (h.x - p.x) + wt * (t.x - p.x),
      y: p.y + wh * (h.y - p.y) + wt * (t.y - p.y),
    }
  }
  return MARK.map((segment) => mapSegment(segment, bend))
}
