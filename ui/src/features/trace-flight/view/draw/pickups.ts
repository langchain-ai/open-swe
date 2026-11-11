/**
 * Pickups: LLM tokens are gray sparkle chips until you fly through one, then
 * they stay in the course in blue with a "+1". Power-ups are round gray tokens
 * marked with their glyph, which the HUD reuses for the active effect.
 */
import { pickupY, type Obstacle, type PowerupKind } from "../../core/course"
import {
  BIRD_X,
  TOKEN_RADIUS,
  POWERUP_RADIUS,
  WORLD_WIDTH,
} from "../../core/rules"
import type { SimState } from "../../core/state"
import type { Palette } from "../palette"
import type { Collected } from "../session"
import { GLYPH_GRID, GLYPH_PATHS, GLYPH_TURN, TOKEN_PATH } from "./glyphs"

/** Ticks the "+1" floats over a collected token. */
const POP_TICKS = 45

let tokenPath: Path2D | null = null

/** An LLM token: a sparkle on a round chip. */
function drawToken(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  mark: string
): void {
  const r = TOKEN_RADIUS * 0.8
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(x, y, r, 0, 2 * Math.PI)
  ctx.fill()
  tokenPath ??= new Path2D(TOKEN_PATH)
  const size = r * 1.3
  ctx.save()
  ctx.translate(x - size / 2, y - size / 2)
  ctx.scale(size / GLYPH_GRID, size / GLYPH_GRID)
  ctx.fillStyle = mark
  ctx.fill(tokenPath)
  ctx.restore()
}

const glyphs = new Map<PowerupKind, Path2D>()

/** A power-up's mark, centered on (x, y) in a box `size` across. */
export function drawGlyph(
  ctx: CanvasRenderingContext2D,
  kind: PowerupKind,
  x: number,
  y: number,
  size: number,
  color: string
): void {
  let path = glyphs.get(kind)
  if (!path) {
    path = new Path2D(GLYPH_PATHS[kind])
    glyphs.set(kind, path)
  }
  ctx.save()
  ctx.translate(x, y)
  ctx.rotate((GLYPH_TURN[kind] * Math.PI) / 180)
  ctx.scale(size / GLYPH_GRID, size / GLYPH_GRID)
  ctx.translate(-GLYPH_GRID / 2, -GLYPH_GRID / 2)
  ctx.fillStyle = color
  ctx.fill(path)
  ctx.restore()
}

/** A round token with a faint halo, so power-ups read as pickups, not obstacles. */
function drawTile(
  ctx: CanvasRenderingContext2D,
  kind: PowerupKind,
  x: number,
  y: number,
  palette: Palette
): void {
  const r = POWERUP_RADIUS
  ctx.fillStyle = palette.pickup
  ctx.beginPath()
  ctx.arc(x, y, r, 0, 2 * Math.PI)
  ctx.fill()
  ctx.save()
  ctx.strokeStyle = palette.pickup
  ctx.globalAlpha = 0.6
  ctx.lineWidth = r * 0.12
  ctx.beginPath()
  ctx.arc(x, y, r * 1.24, 0, 2 * Math.PI)
  ctx.stroke()
  ctx.restore()
  drawGlyph(ctx, kind, x, y, r * 1.16, palette.pickupMark)
}

/** Uncollected pickups at their live positions, including tokens the magnet has pulled loose. */
export function drawPickups(
  ctx: CanvasRenderingContext2D,
  near: readonly Obstacle[],
  sim: SimState | null,
  tick: number,
  scroll: number,
  palette: Palette
): void {
  const taken = new Set(sim?.collected ?? [])
  const loose = new Map((sim?.free ?? []).map((p) => [p.obstacle, p]))
  for (const item of near) {
    const pickup = item.pickup
    if (!pickup || taken.has(item.index)) continue
    const free = loose.get(item.index)
    const x = (free ? free.x : pickup.x) - scroll
    const y = free ? free.y : pickupY(item, pickup, tick)
    if (x < -60 || x > WORLD_WIDTH + 60) continue
    if (pickup.kind === "token")
      drawToken(ctx, x, y, palette.pickup, palette.sky)
    else drawTile(ctx, pickup.kind, x, y, palette)
  }
}

/**
 * While Cache hit is on: a comet trail behind each token the magnet is pulling,
 * from where it was last tick (so it lengthens as the pull speeds up), and a
 * dotted pull from each to the bird.
 */
export function drawMagnet(
  ctx: CanvasRenderingContext2D,
  sim: SimState,
  prev: SimState | null,
  scroll: number,
  birdY: number,
  palette: Palette
): void {
  if (sim.effect.kind !== "cacheHit" || sim.free.length === 0) return
  const before = new Map((prev?.free ?? []).map((p) => [p.obstacle, p]))
  ctx.save()
  ctx.strokeStyle = palette.pickup
  ctx.lineCap = "round"
  for (const p of sim.free) {
    const from = before.get(p.obstacle)
    if (!from) continue
    const tail = ctx.createLinearGradient(
      from.x - scroll,
      from.y,
      p.x - scroll,
      p.y
    )
    tail.addColorStop(0, "transparent")
    tail.addColorStop(1, palette.pickup)
    ctx.strokeStyle = tail
    ctx.globalAlpha = 0.45
    ctx.lineWidth = TOKEN_RADIUS * 1.4
    ctx.beginPath()
    ctx.moveTo(from.x - scroll, from.y)
    ctx.lineTo(p.x - scroll, p.y)
    ctx.stroke()
  }
  ctx.restore()
  ctx.save()
  ctx.strokeStyle = palette.pickup
  ctx.globalAlpha = 0.5
  ctx.lineWidth = 3
  ctx.setLineDash([3, 9])
  ctx.lineCap = "round"
  ctx.beginPath()
  for (const p of sim.free) {
    ctx.moveTo(p.x - scroll, p.y)
    ctx.lineTo(BIRD_X, birdY)
  }
  ctx.stroke()
  ctx.restore()
}

/** Collected tokens stay where they were, in blue; the newest float a "+1". */
export function drawCollected(
  ctx: CanvasRenderingContext2D,
  collected: readonly Collected[],
  tick: number,
  scroll: number,
  palette: Palette,
  reducedMotion: boolean
): void {
  ctx.font = "600 26px ui-monospace, SFMono-Regular, Menlo, monospace"
  ctx.textAlign = "center"
  ctx.textBaseline = "middle"
  for (const c of collected) {
    const x = c.x - scroll
    if (x < -80 || x > WORLD_WIDTH + 80 || c.kind !== "token") continue
    drawToken(ctx, x, c.y, palette.bird, palette.sky)
    const age = tick - c.tick
    if (age >= POP_TICKS) continue
    const t = age / POP_TICKS
    const rise = reducedMotion ? 0 : 34 * Math.sqrt(t)
    ctx.save()
    ctx.globalAlpha = t < 0.66 ? 1 : (1 - t) / 0.34
    ctx.fillStyle = palette.bird
    ctx.beginPath()
    ctx.roundRect(x - 34, c.y + 30 - rise, 68, 40, 20)
    ctx.fill()
    ctx.fillStyle = palette.accentText
    ctx.fillText("+1", x, c.y + 51 - rise)
    ctx.restore()
  }
  ctx.textBaseline = "alphabetic"
}
