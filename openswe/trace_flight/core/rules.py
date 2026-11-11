"""Rules v1, the contract both cores implement; mirrors ui/src/features/trace-flight/core/rules.ts.

Changing any value after launch changes scores, so it needs a new RULES_VERSION
and a new leaderboard season. Positions are world units unless a name says Q8
(world units x 256).
"""

from typing import Final

RULES_VERSION: Final = 1

Q: Final = 256
TICK_CAP: Final = 216_000
FLAP_BUDGET: Final = 20_000

CEILING: Final = 0
FLOOR: Final = 820

BIRD_X: Final = 480
START_Y: Final = 450
BIRD_RADIUS: Final = 35

GRAVITY_Q8: Final = 131
FLAP_VY_Q8: Final = -2560
MAX_FALL_Q8: Final = 3584
SCROLL_Q8: Final = 2048

LEAD: Final = 1600
SPACING: Final = 720
OBSTACLES_PER_LEVEL: Final = 10
GAP_HALF: Final = 150
GAP_MIN: Final = 250
GAP_MAX: Final = 650

WIDTH_PR: Final = 140
WIDTH_TERMINAL: Final = 260
WIDTH_CI: Final = 140
CONFLICT_SECOND_LEFT: Final = 220
CONFLICT_SWAY: Final = 40
CI_GAP_MIN: Final = 340
CI_GAP_MAX: Final = 560
CI_PERIOD: Final = 180

TOKEN_PERCENT: Final = 60
PICKUP_JITTER: Final = 60
TOKEN_RADIUS: Final = 18
POWERUP_RADIUS: Final = 24

START_LIVES: Final = 2
MAX_LIVES: Final = 4
INVULNERABLE_TICKS: Final = 90

DURATION_FORCE_PUSH: Final = 150
DURATION_CACHE_HIT: Final = 360
DURATION_AUTOPILOT: Final = 270

MAGNET_RANGE: Final = 250
# The magnet pulls a token straight at the bird, slowly at first: a step that starts
# at MAGNET_STEP_START and grows by MAGNET_ACCEL a tick, up to MAGNET_STEP_MAX.
MAGNET_STEP_START: Final = 3
MAGNET_ACCEL: Final = 1
MAGNET_STEP_MAX: Final = 32
AUTOPILOT_MARGIN: Final = 20
AUTOPILOT_TAKEOVER_TICKS: Final = 90
AUTOPILOT_HANDOFF_CLEAR: Final = 160

SALT_GAP: Final = 1
SALT_TYPE: Final = 2
SALT_TOKEN: Final = 3
SALT_POWERUP_SLOT: Final = 4
SALT_POWERUP_TYPE: Final = 5
SALT_CI_PHASE: Final = 6
SALT_PICKUP_Y: Final = 7
