/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  addTerminalGroup,
  closeTerminal,
  createTerminalState,
  focusTerminal,
  readTerminalState,
  splitTerminal,
  writeTerminalState,
} from "./terminalState"

beforeEach(() => window.localStorage.clear())

describe("terminal state", () => {
  it("allocates stable term-N ids without reusing closed ids", () => {
    let state = createTerminalState()
    state = splitTerminal(state, "horizontal")
    state = closeTerminal(state, "term-2")
    state = addTerminalGroup(state)

    expect(state.terminalIds).toEqual(["term-1", "term-3"])
    expect(state.activeTerminalId).toBe("term-3")
  })

  it("creates groups and caps a split at four terminals", () => {
    let state = createTerminalState()
    state = splitTerminal(state, "vertical")
    state = splitTerminal(state, "vertical")
    state = splitTerminal(state, "vertical")
    state = splitTerminal(state, "vertical")

    expect(state.terminalIds).toHaveLength(4)
    expect(state.terminalGroups[0]).toEqual({
      id: "group-term-1",
      terminalIds: ["term-1", "term-2", "term-3", "term-4"],
      splitDirection: "vertical",
    })
  })

  it("keeps state isolated and persisted per local session", () => {
    const first = splitTerminal(createTerminalState(), "horizontal")
    const second = focusTerminal(addTerminalGroup(first), "term-1")
    writeTerminalState("session-a", first)
    writeTerminalState("session-b", second)

    expect(readTerminalState("session-a").terminalIds).toEqual([
      "term-1",
      "term-2",
    ])
    expect(readTerminalState("session-b").terminalIds).toEqual([
      "term-1",
      "term-2",
      "term-3",
    ])
    expect(readTerminalState("session-b").activeTerminalId).toBe("term-1")
  })
})
