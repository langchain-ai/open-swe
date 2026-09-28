import { describe, expect, it } from "vitest"

import {
  cdpReconnectDelay,
  classifyBridgeFrame,
  shouldReconnectCdp,
} from "@/features/agents/browser/cdp/cdpConnection"

describe("classifyBridgeFrame", () => {
  it("separates bridge control messages from CDP traffic", () => {
    expect(classifyBridgeFrame(JSON.stringify({ type: "ready" }))).toEqual({
      kind: "control",
      message: { type: "ready" },
    })
    expect(
      classifyBridgeFrame(
        JSON.stringify({ type: "install-output", data: "line" })
      )
    ).toEqual({
      kind: "control",
      message: { type: "install-output", data: "line" },
    })
    expect(
      classifyBridgeFrame(
        JSON.stringify({ id: 4, result: { targetInfos: [] } })
      )
    ).toEqual({ kind: "cdp", message: { id: 4, result: { targetInfos: [] } } })
    expect(
      classifyBridgeFrame(
        JSON.stringify({
          method: "Page.frameNavigated",
          params: {},
          sessionId: "s",
        })
      )
    ).toMatchObject({ kind: "cdp", message: { method: "Page.frameNavigated" } })
  })

  it("drops malformed frames instead of throwing", () => {
    expect(classifyBridgeFrame("not json")).toBeNull()
    expect(classifyBridgeFrame("42")).toBeNull()
    expect(classifyBridgeFrame(JSON.stringify({ type: "unknown" }))).toBeNull()
    expect(classifyBridgeFrame(JSON.stringify({ type: "error" }))).toBeNull()
  })
})

describe("reconnect policy", () => {
  it("never retries normal or policy closes and gives up after five attempts", () => {
    expect(shouldReconnectCdp(1000, 0)).toBe(false)
    expect(shouldReconnectCdp(1008, 0)).toBe(false)
    expect(shouldReconnectCdp(1006, 0)).toBe(true)
    expect(shouldReconnectCdp(1006, 5)).toBe(false)
  })

  it("backs off exponentially with a ceiling", () => {
    expect(cdpReconnectDelay(0)).toBe(500)
    expect(cdpReconnectDelay(2)).toBe(2000)
    expect(cdpReconnectDelay(10)).toBe(10_000)
  })
})
