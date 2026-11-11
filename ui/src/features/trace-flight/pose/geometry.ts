export interface Point {
  readonly x: number
  readonly y: number
}

/** Canvas affine: x' = a·x + c·y + e, y' = b·x + d·y + f. */
export interface Affine {
  readonly a: number
  readonly b: number
  readonly c: number
  readonly d: number
  readonly e: number
  readonly f: number
}

const RADIANS = Math.PI / 180

export function apply(m: Affine, p: Point): Point {
  return { x: m.a * p.x + m.c * p.y + m.e, y: m.b * p.x + m.d * p.y + m.f }
}

/**
 * Turns about `pivot`, then moves by `shift`. Canvas convention: y points down,
 * so positive degrees turn clockwise on screen.
 */
export function rotationAbout(
  pivot: Point,
  degrees: number,
  shift: Point = { x: 0, y: 0 }
): Affine {
  const cos = Math.cos(degrees * RADIANS)
  const sin = Math.sin(degrees * RADIANS)
  return {
    a: cos,
    b: sin,
    c: -sin,
    d: cos,
    e: pivot.x + shift.x - cos * pivot.x + sin * pivot.y,
    f: pivot.y + shift.y - sin * pivot.x - cos * pivot.y,
  }
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

export function lerpPoint(a: Point, b: Point, t: number): Point {
  return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t) }
}

export function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)))
  return t * t * (3 - 2 * t)
}

export function unit(x: number, y: number): Point {
  const length = Math.hypot(x, y)
  return { x: x / length, y: y / length }
}
