/**
 * Autopilot cues: its coming flight path (the sim run ahead with no input),
 * ending in a dot where it hands back if that's in view, and a ring that drains
 * over the effect, then turns the bird's color and pulses faster once a tap can
 * take over. With reduced motion the ring changes color but doesn't pulse.
 */
import {
  Q,
  AUTOPILOT_TAKEOVER_TICKS,
  BIRD_X,
  DURATION,
  type Tuning,
} from "../../core/rules"
import { canTakeOver, step } from "../../core/sim"
import type { SimState } from "../../core/state"
import type { Palette } from "../palette"

/** How far ahead the flight path is drawn, in ticks. */
const PATH_TICKS = 120
/** Pulses per tick at the start and at the end of the take-over window. */
const PULSE_FROM = 1 / 30
const PULSE_TO = 1 / 8

export function drawAutopilot(
  ctx: CanvasRenderingContext2D,
  sim: SimState,
  scroll: number,
  birdY: number,
  options: {
    readonly palette: Palette
    readonly birdSize: number
    readonly tuning: Tuning
    readonly reducedMotion: boolean
  }
): void {
  if (sim.effect.kind !== "autopilot") return
  const { palette, birdSize } = options
  const remaining = sim.effect.remaining
  ctx.save()
  ctx.strokeStyle = palette.autopilot
  ctx.fillStyle = palette.autopilot
  ctx.globalAlpha = 0.7
  ctx.lineWidth = 3
  ctx.lineJoin = "round"
  ctx.setLineDash([4, 10])
  ctx.beginPath()
  ctx.moveTo(BIRD_X, birdY)
  let ahead = sim
  for (let i = 0; i < PATH_TICKS; i++) {
    ahead = step(ahead, false, options.tuning)
    if (ahead.dead) break
    ctx.lineTo(BIRD_X + ahead.d / Q - scroll, ahead.y / Q)
    if (ahead.effect.kind !== "autopilot") break
  }
  ctx.stroke()
  ctx.setLineDash([])
  if (ahead.effect.kind !== "autopilot" && !ahead.dead) {
    ctx.beginPath()
    ctx.arc(BIRD_X + ahead.d / Q - scroll, ahead.y / Q, 7, 0, 2 * Math.PI)
    ctx.fill()
  }

  const radius = birdSize * 0.62
  const left = (remaining - 1) / (DURATION.autopilot - 1)
  const warning = canTakeOver(sim.effect)
  // Integrating a linearly rising rate keeps the pulse smooth as it speeds up;
  // once the timer has run out and Autopilot holds on, it pulses at the top rate.
  const e = AUTOPILOT_TAKEOVER_TICKS - remaining
  const cycles =
    remaining > 1
      ? PULSE_FROM * e +
        ((PULSE_TO - PULSE_FROM) * e * e) / (2 * AUTOPILOT_TAKEOVER_TICKS)
      : PULSE_TO * sim.tick
  const pulse = !warning
    ? 0
    : options.reducedMotion
      ? 1
      : (1 + Math.cos(2 * Math.PI * cycles)) / 2
  ctx.strokeStyle = warning ? palette.bird : palette.autopilot
  ctx.globalAlpha = warning ? 0.25 + 0.6 * pulse : 0.2
  ctx.lineWidth = warning ? 4 + 4 * pulse : 4
  ctx.beginPath()
  ctx.arc(BIRD_X, birdY, radius, 0, 2 * Math.PI)
  ctx.stroke()
  if (left > 0) {
    ctx.globalAlpha = 1
    ctx.lineWidth = 6
    ctx.lineCap = "round"
    ctx.beginPath()
    ctx.arc(
      BIRD_X,
      birdY,
      radius,
      -Math.PI / 2,
      -Math.PI / 2 + left * 2 * Math.PI
    )
    ctx.stroke()
  }
  ctx.restore()
}
