import { describe, expect, it } from "vitest"

import { reconnectLabel } from "./reconnectLabel"
import { MAX_RECONNECT_ATTEMPTS } from "./streamPool"

const NOW = 1_000_000

describe("reconnectLabel", () => {
  it("says nothing while the stream is serving", () => {
    expect(reconnectLabel({ status: "live" }, NOW)).toBeNull()
  })

  it("counts down to the next attempt", () => {
    const label = reconnectLabel(
      {
        status: "reconnecting",
        attempt: 2,
        retryAt: NOW + 4_000,
        source: "sdk",
      },
      NOW
    )

    expect(label).toBe(
      `Reconnecting… 2/${MAX_RECONNECT_ATTEMPTS} (retrying in 4s)`
    )
  })

  it("drops the countdown once the deadline has passed", () => {
    const label = reconnectLabel(
      {
        status: "reconnecting",
        attempt: 2,
        retryAt: NOW - 1,
        source: "sdk",
      },
      NOW
    )

    expect(label).toBe(`Reconnecting… 2/${MAX_RECONNECT_ATTEMPTS}`)
  })

  it("promises a continuation while a recovery chain holds the turn", () => {
    const label = reconnectLabel(
      { status: "reconnecting", attempt: 1, retryAt: 0, source: "resume" },
      NOW
    )

    expect(label).toContain("Continuing automatically")
  })

  it("asks for a new message once recovery gave up", () => {
    const label = reconnectLabel({ status: "failed", message: "timeout" }, NOW)

    expect(label).toContain("Send a message")
  })
})
