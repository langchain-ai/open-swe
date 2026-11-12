/**
 * The wings. The Robin mark has none, so these are ours. Folded, a wing lies
 * inside the body and is never drawn, so the rest pose is still exactly the logo.
 * Opening, it blends from that folded shape to an open wing seen from the side:
 * the span foreshortens with the beat angle, flat toward the viewer at 0°.
 */
import { lerp, lerpPoint, smoothstep, unit, type Point } from "./geometry"

export interface WingShape {
  /** Where the wing's leading edge meets the body, in mark units. */
  readonly shoulder: Point
  /** Scales the wing's length, root to tip. 1 is the default drawing. */
  readonly span: number
  /** Scales the wing's width along the body. */
  readonly chord: number
  /** Degrees the wing rakes back toward its tip. */
  readonly sweep: number
  /** Trailing-edge feather scallops: 0 straight, 1 as drawn, 2 deeper. */
  readonly feathers: number
  /** Width of the cut-out line around the near wing, in mark units. */
  readonly gap: number
  /** The far wing sits a little behind and above the near one, a little smaller. */
  readonly far: {
    readonly dx: number
    readonly dy: number
    readonly scale: number
  }
}

export type WingSide = "near" | "far"

export const DEFAULT_WING: WingShape = {
  shoulder: { x: 575, y: 372 },
  span: 1,
  chord: 1,
  sweep: 0,
  feathers: 1,
  gap: 12,
  far: { dx: 28, dy: -22, scale: 0.9 },
}

/** Along the body toward the tail, and up out of the back. */
const CHORD_AXIS = unit(-1, 0.22)
const SPAN_AXIS = unit(-0.3, -1)

/**
 * Outline points, root → leading edge → tip → trailing edge → back to the root.
 * `folded` is in mark units, inside the body; `open` is (along the body, along
 * the span) from the shoulder. The two lists must stay the same length and order.
 */
const TEMPLATE: readonly {
  readonly folded: Point
  readonly open: Point
  readonly trailing: boolean
}[] = [
  { folded: { x: 640, y: 380 }, open: { x: 0, y: 0 }, trailing: false },
  { folded: { x: 590, y: 335 }, open: { x: 5, y: 80 }, trailing: false },
  { folded: { x: 520, y: 372 }, open: { x: 25, y: 170 }, trailing: false },
  { folded: { x: 450, y: 432 }, open: { x: 60, y: 250 }, trailing: false },
  { folded: { x: 392, y: 500 }, open: { x: 110, y: 310 }, trailing: false },
  { folded: { x: 345, y: 575 }, open: { x: 165, y: 345 }, trailing: false },
  { folded: { x: 305, y: 640 }, open: { x: 215, y: 350 }, trailing: false },
  { folded: { x: 365, y: 618 }, open: { x: 240, y: 300 }, trailing: true },
  { folded: { x: 432, y: 582 }, open: { x: 225, y: 235 }, trailing: true },
  { folded: { x: 500, y: 538 }, open: { x: 235, y: 165 }, trailing: true },
  { folded: { x: 560, y: 480 }, open: { x: 200, y: 95 }, trailing: true },
  { folded: { x: 612, y: 428 }, open: { x: 130, y: 25 }, trailing: false },
]

const TIP = { x: 215, y: 350 }
const TRAILING_ROOT = { x: 130, y: 25 }

/** Along-body position of the straight trailing edge at a span position. */
function trailingLine(v: number): number {
  return lerp(
    TRAILING_ROOT.x,
    TIP.x,
    (v - TRAILING_ROOT.y) / (TIP.y - TRAILING_ROOT.y)
  )
}

export function wingOutline(
  beat: number,
  spread: number,
  shape: WingShape,
  side: WingSide
): readonly Point[] {
  const far = side === "far"
  const scale = far ? shape.far.scale : 1
  const dx = far ? shape.far.dx : 0
  const dy = far ? shape.far.dy : 0
  const lift = Math.sin((beat * Math.PI) / 180)
  const rake = Math.tan((shape.sweep * Math.PI) / 180)
  return TEMPLATE.map(({ folded, open, trailing }) => {
    const along = trailing
      ? trailingLine(open.y) + (open.x - trailingLine(open.y)) * shape.feathers
      : open.x
    const v = open.y * shape.span * scale
    const u = along * shape.chord * scale + rake * v
    const opened = {
      x: shape.shoulder.x + dx + u * CHORD_AXIS.x + v * lift * SPAN_AXIS.x,
      y: shape.shoulder.y + dy + u * CHORD_AXIS.y + v * lift * SPAN_AXIS.y,
    }
    return lerpPoint({ x: folded.x + dx, y: folded.y + dy }, opened, spread)
  })
}

/** The cut-out line fades in as the wing leaves the body, so the logo never shows it. */
export function wingGap(spread: number, shape: WingShape): number {
  return shape.gap * smoothstep(0, 0.2, spread)
}

/** Moves each point of a closed outline outward by `distance`, whichever way it winds. */
export function outset(
  points: readonly Point[],
  distance: number
): readonly Point[] {
  const n = points.length
  const at = (i: number): Point => points[(i + n) % n] ?? { x: 0, y: 0 }
  let doubleArea = 0
  for (let i = 0; i < n; i++)
    doubleArea += at(i).x * at(i + 1).y - at(i + 1).x * at(i).y
  const outward = doubleArea > 0 ? 1 : -1
  // A wing seen edge-on (beat 0°) collapses to a line, so edges can be zero
  // length and neighbouring normals can cancel; never normalize a zero vector.
  const direction = (x: number, y: number): Point =>
    Math.hypot(x, y) > 1e-9 ? unit(x, y) : { x: 0, y: 0 }
  const edgeNormal = (a: Point, b: Point): Point => {
    const perpendicular = direction(b.y - a.y, a.x - b.x)
    return { x: perpendicular.x * outward, y: perpendicular.y * outward }
  }
  return points.map((p, i) => {
    const before = edgeNormal(at(i - 1), p)
    const after = edgeNormal(p, at(i + 1))
    const summed = direction(before.x + after.x, before.y + after.y)
    const normal = summed.x === 0 && summed.y === 0 ? before : summed
    return { x: p.x + normal.x * distance, y: p.y + normal.y * distance }
  })
}
