/**
 * The HUD, in world units so it keeps its place in the letterboxed world:
 * score top left, your best top right, lives as blue pips and the active
 * power-up with its time left, each on a small chip so it reads over obstacles.
 */
import type { PowerupKind } from "../../core/course"
import { DURATION, FLOOR, MAX_LIVES, WORLD_WIDTH } from "../../core/rules"
import type { SimState } from "../../core/state"
import type { Palette } from "../palette"
import { drawGlyph } from "./pickups"

const MARGIN = 48
const CHIP = { height: 56, bottom: FLOOR - 24, radius: 16 }
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"

function chip(
  ctx: CanvasRenderingContext2D,
  x: number,
  width: number,
  palette: Palette
): number {
  const y = CHIP.bottom - CHIP.height
  ctx.save()
  ctx.globalAlpha = 0.9
  ctx.fillStyle = palette.hudChip
  ctx.beginPath()
  ctx.roundRect(x, y, width, CHIP.height, CHIP.radius)
  ctx.fill()
  ctx.restore()
  return y + CHIP.height / 2
}

function drawLives(
  ctx: CanvasRenderingContext2D,
  lives: number,
  palette: Palette
): void {
  const gap = 36
  const width = 28 + gap * (MAX_LIVES - 1) + 28
  const x = WORLD_WIDTH - MARGIN - width
  const cy = chip(ctx, x, width, palette)
  for (let i = 0; i < MAX_LIVES; i++) {
    const cx = x + 28 + i * gap
    ctx.beginPath()
    ctx.arc(cx, cy, 13, 0, 2 * Math.PI)
    if (i < lives) {
      ctx.fillStyle = palette.bird
      ctx.fill()
    } else {
      ctx.strokeStyle = palette.bird
      ctx.globalAlpha = 0.35
      ctx.lineWidth = 3
      ctx.stroke()
      ctx.globalAlpha = 1
    }
  }
}

function drawEffect(
  ctx: CanvasRenderingContext2D,
  kind: PowerupKind,
  left: number,
  palette: Palette
): void {
  const width = 200
  const cy = chip(ctx, MARGIN, width, palette)
  drawGlyph(ctx, kind, MARGIN + 32, cy, 30, palette.hudText)
  const x = MARGIN + 64
  const bar = width - 64 - 24
  ctx.lineCap = "round"
  ctx.lineWidth = 10
  ctx.strokeStyle = palette.paper
  ctx.beginPath()
  ctx.moveTo(x, cy)
  ctx.lineTo(x + bar, cy)
  ctx.stroke()
  ctx.strokeStyle = palette.bird
  ctx.beginPath()
  ctx.moveTo(x, cy)
  ctx.lineTo(x + bar * Math.max(0.02, left), cy)
  ctx.stroke()
}

export function drawHud(
  ctx: CanvasRenderingContext2D,
  sim: SimState | null,
  best: number | null,
  palette: Palette
): void {
  ctx.fillStyle = palette.hudText
  ctx.font = `400 44px ${MONO}`
  ctx.textBaseline = "alphabetic"
  ctx.textAlign = "left"
  ctx.fillText(`Score ${sim?.score ?? 0}`, MARGIN, 84)
  if (best !== null) {
    ctx.textAlign = "right"
    ctx.fillText(`Best ${best}`, WORLD_WIDTH - MARGIN, 84)
  }
  if (!sim) return
  drawLives(ctx, sim.lives, palette)
  const effect = sim.effect
  if (effect.kind !== "none")
    drawEffect(
      ctx,
      effect.kind,
      (effect.remaining - 1) / (DURATION[effect.kind] - 1),
      palette
    )
}
