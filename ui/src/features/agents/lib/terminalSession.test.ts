import { describe, expect, it } from "vitest"

import {
  EMPTY_TERMINAL_SESSION,
  applyTerminalEvent,
  applyTerminalSnapshot,
  cloudTerminalCloseError,
  cloudTerminalProtocols,
} from "./terminalSession"

const snapshot = {
  localSessionId: "session-a",
  terminalId: "term-1",
  cwd: "/tmp/project",
  status: "running" as const,
  pid: 1,
  history: "hello",
  exitCode: null,
  exitSignal: null,
  hasRunningSubprocess: false,
  label: "zsh",
  updatedAt: "2026-08-11T00:00:00Z",
  sequence: 4,
}

describe("terminal session replay", () => {
  it("replays snapshots, appends deltas, and ignores duplicate sequences", () => {
    const initial = applyTerminalSnapshot(EMPTY_TERMINAL_SESSION, snapshot)
    const output = applyTerminalEvent(initial, {
      type: "output",
      localSessionId: "session-a",
      terminalId: "term-1",
      data: " world",
      sequence: 5,
    })
    const duplicate = applyTerminalEvent(output, {
      type: "output",
      localSessionId: "session-a",
      terminalId: "term-1",
      data: " ignored",
      sequence: 5,
    })

    expect(output.buffer).toBe("hello world")
    expect(output.summary).toEqual({
      localSessionId: "session-a",
      terminalId: "term-1",
      cwd: "/tmp/project",
      status: "running",
      pid: 1,
      exitCode: null,
      exitSignal: null,
      hasRunningSubprocess: false,
      label: "zsh",
      updatedAt: "2026-08-11T00:00:00Z",
    })
    expect(duplicate).toBe(output)
  })

  it("aligns restart and activity event identities with snapshots", () => {
    const initial = applyTerminalSnapshot(EMPTY_TERMINAL_SESSION, snapshot)
    const active = applyTerminalEvent(initial, {
      type: "activity",
      localSessionId: "session-a",
      terminalId: "term-1",
      hasRunningSubprocess: true,
      label: "node",
      sequence: 5,
    })
    const restarted = applyTerminalEvent(active, {
      type: "restarted",
      localSessionId: "session-a",
      terminalId: "term-1",
      sequence: 6,
      snapshot: {
        ...snapshot,
        pid: 2,
        history: "new",
        sequence: 6,
      },
    })

    expect(active.summary?.hasRunningSubprocess).toBe(true)
    expect(active.summary?.label).toBe("node")
    expect(restarted.buffer).toBe("new")
    expect(restarted.summary?.pid).toBe(2)
  })
})

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
})
