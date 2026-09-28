import { describe, expect, it } from "vitest"

import {
  INITIAL_WEBVIEW_CRASH_RECOVERY_STATE,
  WEBVIEW_CRASH_RECOVERY_WINDOW_MS,
  planWebviewCrashRecovery,
} from "@/features/agents/browser/desktop/webviewCrashRecovery"

describe("planWebviewCrashRecovery", () => {
  it("backs off and gives up after three crashes in a window", () => {
    const first = planWebviewCrashRecovery(
      INITIAL_WEBVIEW_CRASH_RECOVERY_STATE,
      1000
    )
    expect(first?.delayMs).toBe(250)
    const second = planWebviewCrashRecovery(first!.state, 2000)
    expect(second?.delayMs).toBe(500)
    const third = planWebviewCrashRecovery(second!.state, 3000)
    expect(third?.delayMs).toBe(1000)
    expect(planWebviewCrashRecovery(third!.state, 4000)).toBeNull()
  })

  it("starts a fresh window once the old one expires", () => {
    const exhausted = { attempts: 3, windowStartedAt: 0 }
    expect(
      planWebviewCrashRecovery(exhausted, WEBVIEW_CRASH_RECOVERY_WINDOW_MS)
    ).toMatchObject({ delayMs: 250, state: { attempts: 1 } })
  })
})
