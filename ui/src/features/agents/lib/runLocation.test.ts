import { describe, expect, it } from "vitest"

import type { AgentThread } from "@/features/agents/lib/types"
import {
  canRunThread,
  isLocalThread,
  runsElsewhereLabel,
} from "./runLocation"

const localThread = {
  runLocation: "local",
  deviceId: "abc123",
  deviceName: "Work laptop",
} as AgentThread

describe("run location", () => {
  it("treats a thread without a run location as a cloud thread", () => {
    expect(isLocalThread({} as AgentThread)).toBe(false)
    expect(isLocalThread({ runLocation: "cloud" } as AgentThread)).toBe(false)
    expect(isLocalThread(localThread)).toBe(true)
  })

  it("lets any client drive a cloud thread", () => {
    const cloud = { runLocation: "cloud" } as AgentThread
    expect(canRunThread(cloud, null)).toBe(true)
    expect(canRunThread(cloud, "abc123")).toBe(true)
  })

  it("only lets the owning machine drive a local thread", () => {
    expect(canRunThread(localThread, "abc123")).toBe(true)
    expect(canRunThread(localThread, "other-device")).toBe(false)
    // The web has no device identity at all.
    expect(canRunThread(localThread, null)).toBe(false)
    expect(canRunThread(localThread, undefined)).toBe(false)
  })

  it("names the machine a thread belongs to", () => {
    expect(runsElsewhereLabel(localThread)).toBe("Runs on Work laptop")
    expect(runsElsewhereLabel({} as AgentThread)).toBe(
      "Runs on another computer"
    )
  })
})
