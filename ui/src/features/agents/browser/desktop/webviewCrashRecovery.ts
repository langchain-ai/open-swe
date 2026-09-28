export const WEBVIEW_CRASH_RECOVERY_WINDOW_MS = 30_000
const WEBVIEW_CRASH_RECOVERY_MAX_ATTEMPTS = 3
const WEBVIEW_CRASH_RECOVERY_BASE_DELAY_MS = 250

export interface WebviewCrashRecoveryState {
  readonly attempts: number
  readonly windowStartedAt: number | null
}

export const INITIAL_WEBVIEW_CRASH_RECOVERY_STATE: WebviewCrashRecoveryState = {
  attempts: 0,
  windowStartedAt: null,
}

/**
 * At most three remounts per thirty seconds with exponential backoff; a page
 * that keeps crashing its renderer stays down rather than looping.
 */
export function planWebviewCrashRecovery(
  state: WebviewCrashRecoveryState,
  now: number
): {
  readonly delayMs: number
  readonly state: WebviewCrashRecoveryState
} | null {
  const startsNewWindow =
    state.windowStartedAt === null ||
    now - state.windowStartedAt >= WEBVIEW_CRASH_RECOVERY_WINDOW_MS
  const attempts = startsNewWindow ? 0 : state.attempts
  if (attempts >= WEBVIEW_CRASH_RECOVERY_MAX_ATTEMPTS) return null
  return {
    delayMs: WEBVIEW_CRASH_RECOVERY_BASE_DELAY_MS * 2 ** attempts,
    state: {
      attempts: attempts + 1,
      windowStartedAt: startsNewWindow ? now : state.windowStartedAt,
    },
  }
}
