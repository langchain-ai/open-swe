/**
 * One frame of the game, back to front: the world, Bufo cameos, your best
 * run's trace, obstacles, pickups, the ground, the bird and its cues, then the
 * HUD. Positions blend between the last two sim states; nothing here feeds back
 * into the sim.
 */
import { divFloor } from "../../core/arith"
import { gapAt, obstacle } from "../../core/course"
import {
  BIRD_X,
  CEILING,
  FLOOR,
  GAP_HALF,
  LEAD,
  Q,
  SPACING,
  START_Y,
  WORLD_HEIGHT,
  WORLD_WIDTH,
  type Tuning,
} from "../../core/rules"
import type { SimState } from "../../core/state"
import type { BirdPose } from "../../pose/rig"
import type { WingShape } from "../../pose/wings"
import { drawBird } from "../drawBird"
import type { Palette } from "../palette"
import type { Collected } from "../session"
import type { Trace } from "../trace"
import { drawAutopilot } from "./autopilot"
import { drawBestTrace } from "./bestTrace"
import { drawBufos } from "./bufo"
import { drawHud } from "./hud"
import { drawObstacle } from "./obstacles"
import { drawCollected, drawMagnet, drawPickups } from "./pickups"
import { drawBackdrop, drawGround, projection } from "./world"

export interface SceneFrame {
  /** The latest sim state, or null on the Ready screen before the first flap. */
  readonly sim: SimState | null
  readonly prev: SimState | null
  /** Fraction of the way from prev to sim. */
  readonly t: number
  readonly pose: BirdPose
  readonly ended: boolean
  readonly collected: readonly Collected[]
  readonly trace: Trace | null
  readonly best: number | null
  /** Opening or leaving the game; absent while you play. */
  readonly launch?: SceneLaunch
}

/** The game fading over the page, and the bird leaping between the logo and the course. */
export interface SceneLaunch {
  /** How much of the game shows, 0 to 1; the page shows through the rest. */
  readonly fade: number
  /**
   * The logo the bird lives in, canvas pixels; how far along the leap the bird is
   * (0 at the logo, past 1 while it overshoots); how high it arcs, world units;
   * and its pose when the leap has its own.
   */
  readonly leap?: {
    readonly x: number
    readonly y: number
    readonly size: number
    readonly along: number
    readonly lift: number
    readonly pose?: BirdPose
  }
}

export interface SceneOptions {
  readonly palette: Palette
  readonly reducedMotion: boolean
  readonly seed: number
  readonly tuning: Tuning
  /** Drawn width of the bird, world units. */
  readonly birdSize: number
  readonly wing: WingShape
  /** Harness overlays: the collision boxes and the bird's hit circle. */
  readonly debug?: { readonly boxes: boolean; readonly hitbox: boolean }
}

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"
const SANS = "ui-sans-serif, system-ui, sans-serif"

function drawStreaks(
  ctx: CanvasRenderingContext2D,
  birdY: number,
  tick: number,
  palette: Palette
): void {
  ctx.save()
  ctx.strokeStyle = palette.bird
  ctx.globalAlpha = 0.55
  ctx.lineWidth = 4
  ctx.lineCap = "round"
  ctx.beginPath()
  for (const [dy, length, phase] of [
    [-26, 220, 0],
    [-4, 300, 7],
    [18, 180, 13],
  ] as const) {
    const shift = ((tick * 9 + phase * 11) % 60) - 30
    ctx.moveTo(BIRD_X - 70 + shift, birdY + dy)
    ctx.lineTo(BIRD_X - 70 - length + shift, birdY + dy)
  }
  ctx.stroke()
  ctx.restore()
}

function drawDebug(
  ctx: CanvasRenderingContext2D,
  near: readonly ReturnType<typeof obstacle>[],
  tick: number,
  scroll: number,
  birdY: number,
  options: SceneOptions
): void {
  const debug = options.debug
  if (!debug) return
  ctx.save()
  ctx.strokeStyle = options.palette.hudText
  ctx.lineWidth = 2
  if (debug.boxes) {
    ctx.setLineDash([8, 6])
    for (const item of near)
      for (const block of item.blocks) {
        const x = block.left - scroll
        const gap = gapAt(block, tick)
        ctx.strokeRect(x, CEILING, block.width, gap - GAP_HALF - CEILING)
        ctx.strokeRect(x, gap + GAP_HALF, block.width, FLOOR - gap - GAP_HALF)
      }
    ctx.setLineDash([])
  }
  if (debug.hitbox) {
    ctx.beginPath()
    ctx.arc(BIRD_X, birdY, options.tuning.birdRadius, 0, 2 * Math.PI)
    ctx.stroke()
  }
  ctx.restore()
}

export function drawScene(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  frame: SceneFrame,
  options: SceneOptions
): void {
  const { palette, reducedMotion } = options
  const { scale, ox, oy } = projection(width, height)
  const sim = frame.sim
  const prev = frame.prev ?? sim
  const blend = (a: number, b: number) => a + (b - a) * frame.t
  const scroll = sim && prev ? blend(prev.d, sim.d) / Q : 0
  const birdY = sim && prev ? blend(prev.y, sim.y) / Q : START_Y
  const tick = sim?.tick ?? 0
  const mode = sim?.effect.kind ?? "none"

  ctx.fillStyle = palette.sky
  ctx.fillRect(0, 0, width, height)
  ctx.save()
  ctx.translate(ox, oy)
  ctx.scale(scale, scale)
  ctx.beginPath()
  ctx.rect(0, 0, WORLD_WIDTH, WORLD_HEIGHT)
  ctx.clip()

  drawBackdrop(ctx, scroll, palette, reducedMotion)

  const first = Math.max(0, divFloor(scroll - 400 - LEAD, SPACING))
  const last = divFloor(scroll + WORLD_WIDTH - LEAD, SPACING)
  drawBufos(ctx, options.seed, first, last, scroll, tick, reducedMotion)
  if (frame.trace) drawBestTrace(ctx, frame.trace, scroll, palette)
  const near = Array.from({ length: Math.max(0, last - first + 1) }, (_, k) =>
    obstacle(options.seed, first + k)
  )
  ctx.save()
  // Force push flies straight through: obstacles fade back while it lasts.
  if (mode === "forcePush") ctx.globalAlpha = 0.35
  for (const item of near) drawObstacle(ctx, item, tick, scroll, palette)
  ctx.restore()
  drawPickups(ctx, near, sim, tick, scroll, palette)
  drawCollected(ctx, frame.collected, tick, scroll, palette, reducedMotion)
  drawGround(ctx, scroll, palette)

  if (sim) {
    drawMagnet(ctx, sim, frame.prev, scroll, birdY, palette)
    if (mode === "forcePush" && !reducedMotion)
      drawStreaks(ctx, birdY, tick, palette)
    drawAutopilot(ctx, sim, scroll, birdY, options)
  }
  if (!frame.launch?.leap)
    drawBird(ctx, frame.pose, {
      x: BIRD_X,
      y: birdY,
      size: options.birdSize,
      fill: palette.bird,
      wing: options.wing,
    })
  drawDebug(ctx, near, tick, scroll, birdY, options)

  if (frame.ended) {
    ctx.fillStyle = palette.sky
    ctx.globalAlpha = 0.6
    ctx.fillRect(0, 0, WORLD_WIDTH, WORLD_HEIGHT)
    ctx.globalAlpha = 1
  } else drawHud(ctx, sim, frame.best, palette)

  if (!sim) {
    ctx.fillStyle = palette.hudText
    ctx.textAlign = "center"
    ctx.font = `400 30px ${SANS}`
    ctx.fillText("Press Space or tap to fly", WORLD_WIDTH / 2, START_Y + 140)
    ctx.font = `400 20px ${MONO}`
    ctx.globalAlpha = 0.7
    ctx.fillText("Esc to leave", WORLD_WIDTH / 2, START_Y + 182)
    ctx.globalAlpha = 1
  }
  ctx.restore()

  const launch = frame.launch
  if (!launch) return
  // Erasing what's drawn fades the world, HUD and bird alike; the leaping bird goes on top.
  ctx.save()
  ctx.globalCompositeOperation = "destination-out"
  ctx.globalAlpha = 1 - launch.fade
  ctx.fillRect(0, 0, width, height)
  ctx.restore()
  const leap = launch.leap
  if (!leap) return
  const along = leap.along
  const homeX = (leap.x - ox) / scale
  const homeY = (leap.y - oy) / scale
  const homeSize = leap.size / scale
  ctx.save()
  ctx.translate(ox, oy)
  ctx.scale(scale, scale)
  drawBird(ctx, leap.pose ?? frame.pose, {
    x: homeX + (BIRD_X - homeX) * along,
    y: homeY + (birdY - homeY) * along - leap.lift,
    size: homeSize + (options.birdSize - homeSize) * Math.min(1, along),
    fill: palette.bird,
    wing: options.wing,
  })
  ctx.restore()
}
