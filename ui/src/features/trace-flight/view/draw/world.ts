/**
 * The cut-paper world behind the course: a code minimap far back, paper shapes
 * nearer, and the ground. Layers scroll slower the farther back they are; with
 * reduced motion they hold still and only the ground moves with the course.
 */
import { mix32 } from "../../core/arith"
import { FLOOR, WORLD_HEIGHT, WORLD_WIDTH } from "../../core/rules"
import type { Palette } from "../palette"

/** World-to-canvas: the 1600 × 900 world fits the canvas, centered. */
export function projection(width: number, height: number) {
  const scale = Math.min(width / WORLD_WIDTH, height / WORLD_HEIGHT)
  return {
    scale,
    ox: (width - WORLD_WIDTH * scale) / 2,
    oy: (height - WORLD_HEIGHT * scale) / 2,
  }
}

const MINIMAP = { every: 260, speed: 0.3, top: 330, rows: 22, rowGap: 19 }
const PAPER = { every: 420, speed: 0.55 }
const LANE = { every: 180, length: 110, y: 862 }

/** Cosmetic variety that stays put as the world scrolls past. */
function noise(column: number, salt: number): number {
  return mix32(mix32(column >>> 0) ^ salt) / 0x100000000
}

export function drawBackdrop(
  ctx: CanvasRenderingContext2D,
  scroll: number,
  palette: Palette,
  reducedMotion: boolean
): void {
  ctx.fillStyle = palette.sky
  ctx.fillRect(0, 0, WORLD_WIDTH, WORLD_HEIGHT)
  const depth = (speed: number) => (reducedMotion ? 0 : scroll * speed)

  ctx.strokeStyle = palette.minimap
  ctx.lineWidth = 9
  ctx.lineCap = "round"
  const far = depth(MINIMAP.speed)
  for (
    let c = Math.floor(far / MINIMAP.every) - 1;
    c * MINIMAP.every - far < WORLD_WIDTH;
    c++
  ) {
    if (noise(c, 1) < 0.3) continue
    const x = c * MINIMAP.every - far
    ctx.beginPath()
    for (let r = 0; r < MINIMAP.rows; r++) {
      if (noise(c * 64 + r, 2) < 0.12) continue
      const indent = Math.floor(noise(c * 64 + r, 3) * 4) * 14
      const length = 30 + noise(c * 64 + r, 4) * 100
      const y = MINIMAP.top + r * MINIMAP.rowGap
      ctx.moveTo(x + indent, y)
      ctx.lineTo(x + indent + length, y)
    }
    ctx.stroke()
  }

  ctx.fillStyle = palette.paper
  const near = depth(PAPER.speed)
  for (
    let c = Math.floor(near / PAPER.every) - 1;
    c * PAPER.every - near < WORLD_WIDTH;
    c++
  ) {
    const width = 160 + noise(c, 5) * 180
    const height = 80 + noise(c, 6) * 150
    const x = c * PAPER.every - near + noise(c, 7) * 120
    ctx.beginPath()
    ctx.roundRect(x, FLOOR - height, width, height + 20, [26, 26, 0, 0])
    ctx.fill()
  }
}

export function drawGround(
  ctx: CanvasRenderingContext2D,
  scroll: number,
  palette: Palette
): void {
  ctx.fillStyle = palette.ground
  ctx.fillRect(0, FLOOR, WORLD_WIDTH, WORLD_HEIGHT - FLOOR)
  ctx.fillStyle = palette.groundEdge
  ctx.fillRect(0, FLOOR, WORLD_WIDTH, 5)
  ctx.strokeStyle = palette.lane
  ctx.lineWidth = 7
  ctx.lineCap = "round"
  ctx.beginPath()
  const offset = scroll % LANE.every
  for (let x = -offset; x < WORLD_WIDTH; x += LANE.every) {
    ctx.moveTo(x, LANE.y)
    ctx.lineTo(x + LANE.length, LANE.y)
  }
  ctx.stroke()
}
