/** Damped springs stepped at the game's fixed tick, so every frame is reproducible. */

export const TICKS_PER_SECOND = 60
export const TICK_SECONDS = 1 / TICKS_PER_SECOND

export interface Spring {
  readonly value: number
  /** Units per second. */
  readonly velocity: number
}

export interface SpringTuning {
  /** Natural frequency in Hz: higher snaps back faster. */
  readonly frequency: number
  /** Damping ratio: below 1 overshoots, 1 settles without overshoot. */
  readonly damping: number
}

export const AT_REST: Spring = { value: 0, velocity: 0 }

const MAX_SUBSTEPS = 64

/**
 * Semi-implicit Euler with step h is stable when, with w = 2π · frequency,
 * `2·damping·w·h < 2` and `(w·h)² + 4·damping·w·h < 4`. The margin keeps clear
 * of the edge, where the spring rings without growing.
 */
function stableAt(omega: number, damping: number, h: number): boolean {
  const stiff = (omega * h) ** 2
  const drag = 2 * damping * omega * h
  return drag < 1.8 && stiff + 2 * drag < 3.6
}

/**
 * Splits a tick into substeps only for tunings outside the stability margin, so
 * every lab setting stays finite and tunings inside it keep their exact motion.
 */
function substepsFor(omega: number, damping: number): number {
  let n = 1
  while (n < MAX_SUBSTEPS && !stableAt(omega, damping, TICK_SECONDS / n)) n++
  return n
}

export function stepSpring(
  spring: Spring,
  target: number,
  tuning: SpringTuning
): Spring {
  const omega = 2 * Math.PI * tuning.frequency
  const n = substepsFor(omega, tuning.damping)
  const h = TICK_SECONDS / n
  let { value, velocity } = spring
  for (let i = 0; i < n; i++) {
    const acceleration =
      -omega * omega * (value - target) - 2 * tuning.damping * omega * velocity
    velocity += acceleration * h
    value += velocity * h
  }
  return { value, velocity }
}

/** Adds to velocity and never resets position, so repeated impulses blend. */
export function kick(spring: Spring, impulse: number): Spring {
  return { value: spring.value, velocity: spring.velocity + impulse }
}
