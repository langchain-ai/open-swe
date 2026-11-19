import { describe, expect, it } from "vitest"

import {
  cloudTerminalCloseError,
  cloudTerminalProtocols,
  shouldReconnectCloudTerminal,
} from "./terminalSession"

describe("cloud terminal connection", () => {
  it("passes the short-lived ticket as a WebSocket subprotocol", () => {
    expect(
      cloudTerminalProtocols({
        url: "wss://agent.example/terminal",
        protocol: "open-swe-terminal",
        ticket: "signed-ticket",
      })
    ).toEqual(["open-swe-terminal", "signed-ticket"])
  })

  it("keeps the actionable handshake error when close has no reason", () => {
    expect(
      cloudTerminalCloseError("", "Unable to connect to cloud terminal")
    ).toBe("Unable to connect to cloud terminal")
    expect(cloudTerminalCloseError("capacity reached", "prior error")).toBe(
      "capacity reached"
    )
    expect(cloudTerminalCloseError("", null)).toBe(
      "Cloud terminal disconnected"
    )
  })

  it("retries transient closes but not policy failures or attempt exhaustion", () => {
    expect(shouldReconnectCloudTerminal(1013, 0)).toBe(true)
    expect(shouldReconnectCloudTerminal(1006, 4)).toBe(true)
    expect(shouldReconnectCloudTerminal(1008, 0)).toBe(false)
    expect(shouldReconnectCloudTerminal(1000, 0)).toBe(false)
    expect(shouldReconnectCloudTerminal(1006, 5)).toBe(false)
  })
})
