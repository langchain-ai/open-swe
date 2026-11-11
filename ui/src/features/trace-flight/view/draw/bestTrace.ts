/** Your best run as a faint dotted line along the course, behind and ahead of you. */
import { BIRD_X, WORLD_WIDTH } from "../../core/rules"
import type { Palette } from "../palette"
import { TRACE_STEP, type Trace } from "../trace"

/** One dot every this many samples (24 world units). */
const DOT_EVERY = 3

export function drawBestTrace(
  ctx: CanvasRenderingContext2D,
  trace: Trace,
  scroll: number,
  palette: Palette
): void {
  const { ys } = trace
  const first = Math.max(0, Math.floor((scroll - BIRD_X) / TRACE_STEP))
  const last = Math.min(
    ys.length - 1,
    Math.ceil((scroll + WORLD_WIDTH - BIRD_X) / TRACE_STEP)
  )
  ctx.save()
  ctx.fillStyle = palette.bird
  ctx.globalAlpha = 0.45
  ctx.beginPath()
  // Dots keep their place on the course rather than on the screen.
  for (let k = first - (first % DOT_EVERY); k <= last; k += DOT_EVERY) {
    const y = ys[k]
    if (y === undefined) continue
    const x = BIRD_X + k * TRACE_STEP - scroll
    ctx.moveTo(x + 3.2, y)
    ctx.arc(x, y, 3.2, 0, 2 * Math.PI)
  }
  ctx.fill()
  ctx.restore()
}
