/**
 * Rules v1: the contract the TypeScript and Python cores both implement.
 * Changing any value after launch changes scores, so it needs a new
 * RULES_VERSION and a new leaderboard season.
 *
 * Units: positions are world units (the world is 1600 × 900) unless a name says
 * Q8; Q8 values are world units × 256, so the sim stays in integers.
 */
export const RULES_VERSION = 1

export const Q = 256
/** 60 minutes of play; shown as "context window full". */
export const TICK_CAP = 216_000
/** Flaps the sim accepts per run; keeps every submission under the request limit. */
export const FLAP_BUDGET = 20_000

export const WORLD_WIDTH = 1600
export const WORLD_HEIGHT = 900
export const CEILING = 0
export const FLOOR = 820

export const BIRD_X = 480
export const START_Y = 450
export const BIRD_RADIUS = 35

/** What the harness may change while tuning. Runs and replays always use DEFAULT_TUNING. */
export interface Tuning {
  readonly birdRadius: number
}

export const DEFAULT_TUNING: Tuning = { birdRadius: BIRD_RADIUS }

/** Q8 per tick² and Q8 per tick. */
export const GRAVITY_Q8 = 131
export const FLAP_VY_Q8 = -2560
export const MAX_FALL_Q8 = 3584
export const SCROLL_Q8 = 2048

/** The first obstacle enters at the right edge of the screen. */
export const LEAD = 1600
export const SPACING = 720
export const OBSTACLES_PER_LEVEL = 10
export const GAP_HALF = 150
export const GAP_MIN = 250
export const GAP_MAX = 650

export const WIDTH = { pr: 140, terminal: 260, ci: 140 } as const
/** Merge conflict: two PR-width blocks, the second starting this far after the first. */
export const CONFLICT_SECOND_LEFT = 220
/**
 * The S: gaps sit this far above and below the obstacle's gap. ±70 (rev 5) left
 * only 100 units where the bird clears both blocks, less than one flap's rise.
 */
export const CONFLICT_SWAY = 40
export const CI_GAP_MIN = 340
export const CI_GAP_MAX = 560
export const CI_PERIOD = 180

export const TOKEN_PERCENT = 60
export const PICKUP_JITTER = 60
export const TOKEN_RADIUS = 18
export const POWERUP_RADIUS = 24

export const START_LIVES = 2
export const MAX_LIVES = 4
export const INVULNERABLE_TICKS = 90

export const DURATION = {
  forcePush: 150,
  cacheHit: 360,
  autopilot: 270,
} as const

export const MAGNET_RANGE = 250
/**
 * The magnet pulls a token straight at the bird, slowly at first: a step that
 * starts at MAGNET_STEP_START and grows by MAGNET_ACCEL a tick, up to
 * MAGNET_STEP_MAX. World units, integer arithmetic.
 */
export const MAGNET_STEP_START = 3
export const MAGNET_ACCEL = 1
export const MAGNET_STEP_MAX = 32
export const AUTOPILOT_MARGIN = 20
/** In Autopilot's last ticks, and while it holds on past them, a flap takes over. */
export const AUTOPILOT_TAKEOVER_TICKS = 90
/**
 * Autopilot hands back only when no block lies within this many world units
 * ahead of the bird. The narrowest open stretch (between obstacles, 360 u)
 * must stay wider than this plus the bird, or the hand-off could wait forever.
 */
export const AUTOPILOT_HANDOFF_CLEAR = 160

/** hash32 salts; per-obstacle values hash the obstacle index, per-level values the level. */
export const SALT = {
  gap: 1,
  type: 2,
  token: 3,
  powerupSlot: 4,
  powerupType: 5,
  ciPhase: 6,
  pickupY: 7,
} as const
