import type { Point } from "../pose/geometry"
import { MARK, MARK_BOX, MARK_CENTER, type Segment } from "../pose/mark"
import type { JointPose } from "../pose/poses"
import type { BirdPose, Lids } from "../pose/rig"
import {
  bendMark,
  EYE,
  HEAD_PIVOT,
  headAffine,
  NECK,
  TAIL_PIVOT,
  TAIL_ROOT,
  type Band,
} from "../pose/robin"
import { outset, wingGap, wingOutline, type WingShape } from "../pose/wings"

/** Drawn width of the bird in the game, world units. */
export const BIRD_SIZE = 110

const DEGREES = Math.PI / 180
/** Joints closer to rest than this draw the cached mark. */
const AT_REST = 1e-3

export interface BirdPlacement {
  /** Canvas position of the bird's center. */
  readonly x: number
  readonly y: number
  /** Rendered width of the mark, in canvas pixels. */
  readonly size: number
  readonly fill: string
  readonly wing: WingShape
  /** When set, draws the joint pivots and blend bands over the bird in this color. */
  readonly rigColor?: string
}

function tracePath(segments: readonly Segment[]): Path2D {
  const path = new Path2D()
  for (const s of segments) {
    if (s.kind === "move") path.moveTo(s.to.x, s.to.y)
    else if (s.kind === "curve")
      path.bezierCurveTo(s.c1.x, s.c1.y, s.c2.x, s.c2.y, s.to.x, s.to.y)
    else path.closePath()
  }
  return path
}

/** A closed curve through the midpoints of a point list, using the points as handles. */
function smoothOutline(points: readonly Point[]): Path2D {
  const path = new Path2D()
  const n = points.length
  const at = (i: number): Point => points[(i + n) % n] ?? { x: 0, y: 0 }
  const mid = (a: Point, b: Point): Point => ({
    x: (a.x + b.x) / 2,
    y: (a.y + b.y) / 2,
  })
  const start = mid(at(-1), at(0))
  path.moveTo(start.x, start.y)
  for (let i = 0; i < n; i++) {
    const end = mid(at(i), at(i + 1))
    path.quadraticCurveTo(at(i).x, at(i).y, end.x, end.y)
  }
  path.closePath()
  return path
}

let restingMark: Path2D | null = null

function bodyPath(joints: JointPose): Path2D {
  const resting =
    Math.abs(joints.head) < AT_REST &&
    Math.abs(joints.headReach) < AT_REST &&
    Math.abs(joints.tail) < AT_REST
  if (!resting) return tracePath(bendMark(joints))
  restingMark ??= tracePath(MARK)
  return restingMark
}

function drawEyelid(
  ctx: CanvasRenderingContext2D,
  joints: JointPose,
  eye: number,
  lids: Lids,
  fill: string
): void {
  if (eye <= 0.01) return
  const m = headAffine(joints)
  const r = EYE.radius + 2
  ctx.save()
  ctx.transform(m.a, m.b, m.c, m.d, m.e, m.f)
  ctx.beginPath()
  ctx.arc(EYE.x, EYE.y, r, 0, 2 * Math.PI)
  ctx.clip()
  ctx.fillStyle = fill
  const cover = 2 * r * Math.min(1, eye)
  if (lids === "upper") ctx.fillRect(EYE.x - r, EYE.y - r, 2 * r, cover)
  else {
    ctx.fillRect(EYE.x - r, EYE.y - r, 2 * r, cover / 2)
    ctx.fillRect(EYE.x - r, EYE.y + r - cover / 2, 2 * r, cover / 2)
  }
  ctx.restore()
}

function drawRig(ctx: CanvasRenderingContext2D, color: string): void {
  const band = (b: Band) => {
    const along = { x: -b.normal.y, y: b.normal.x }
    ctx.beginPath()
    for (const offset of [-b.halfWidth, 0, b.halfWidth]) {
      const cx = b.origin.x + b.normal.x * offset
      const cy = b.origin.y + b.normal.y * offset
      ctx.moveTo(cx - along.x * 140, cy - along.y * 140)
      ctx.lineTo(cx + along.x * 140, cy + along.y * 140)
    }
    ctx.stroke()
  }
  ctx.save()
  ctx.strokeStyle = color
  ctx.fillStyle = color
  ctx.lineWidth = 3
  ctx.setLineDash([10, 10])
  band(NECK)
  band(TAIL_ROOT)
  ctx.setLineDash([])
  for (const p of [HEAD_PIVOT, TAIL_PIVOT]) {
    ctx.beginPath()
    ctx.arc(p.x, p.y, 12, 0, 2 * Math.PI)
    ctx.fill()
  }
  ctx.restore()
}

/** Draws the bird in mark units: far wing, body, eyelid, then the near wing with its cut-out line. */
function drawRobin(
  ctx: CanvasRenderingContext2D,
  joints: JointPose,
  eye: number,
  lids: Lids,
  wing: WingShape,
  fill: string
): void {
  const open = joints.spread > AT_REST
  const near = open
    ? wingOutline(joints.beat, joints.spread, wing, "near")
    : null
  const gap = wingGap(joints.spread, wing)
  ctx.save()
  ctx.fillStyle = fill
  if (near && gap > 0.2) {
    // Everything except the near wing's outline grown by the gap: the cut-out
    // stays transparent over whatever is behind the bird.
    const keep = new Path2D()
    keep.rect(
      -MARK_BOX.width,
      -MARK_BOX.height,
      MARK_BOX.width * 4,
      MARK_BOX.height * 4
    )
    keep.addPath(smoothOutline(outset(near, gap)))
    ctx.clip(keep, "evenodd")
  }
  if (open)
    ctx.fill(
      smoothOutline(wingOutline(joints.beat, joints.spread, wing, "far"))
    )
  ctx.fill(bodyPath(joints), "evenodd")
  drawEyelid(ctx, joints, eye, lids, fill)
  ctx.restore()
  if (near) {
    ctx.fillStyle = fill
    ctx.fill(smoothOutline(near))
  }
}

export function drawBird(
  ctx: CanvasRenderingContext2D,
  pose: BirdPose,
  at: BirdPlacement
): void {
  const unit = at.size / MARK_BOX.width
  ctx.save()
  ctx.globalAlpha *= pose.alpha
  ctx.translate(at.x, at.y + pose.offsetY * unit)
  ctx.rotate(pose.angle * DEGREES)
  ctx.scale(pose.scaleX * unit, pose.scaleY * unit)
  ctx.translate(-MARK_CENTER.x, -MARK_CENTER.y)
  drawRobin(ctx, pose.joints, pose.eye, pose.lids, at.wing, at.fill)
  if (at.rigColor) drawRig(ctx, at.rigColor)
  ctx.restore()
}
