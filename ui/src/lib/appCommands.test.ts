/** @vitest-environment jsdom */

import { describe, expect, it, vi } from "vitest"

import { createNewThreadCommand, resolveAppCommands } from "./appCommands"
import { eventMatchesShortcut } from "./hotkeys"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AppCommand } from "./appCommands"
import { buildPaletteResults } from "@/components/AppCommandPalette"

function command(id: string, label = id): AppCommand {
  return { id, label, group: "General", run: vi.fn() }
}

describe("app commands", () => {
  it("requires a modifier to create a new thread", () => {
    const newThread = createNewThreadCommand(vi.fn())
    const matches = (key: string, ctrlKey = false, shiftKey = false) =>
      newThread.shortcuts?.some((shortcut) =>
        eventMatchesShortcut(
          new KeyboardEvent("keydown", { key, ctrlKey, shiftKey }),
          shortcut,
          "other"
        )
      )

    expect(matches("c")).toBe(false)
    expect(matches("n", true)).toBe(true)
    expect(matches("o", true, true)).toBe(true)
  })

  it("lets the latest contextual registration replace a command", () => {
    const global = [command("new-thread")]
    const contextual = command("toggle-sidebar", "First sidebar")
    const replacement = command("toggle-sidebar", "Current sidebar")

    const resolved = resolveAppCommands(global, [
      { key: 1, commands: [contextual] },
      { key: 2, commands: [replacement] },
    ])

    expect(resolved.map((item) => item.id)).toEqual([
      "new-thread",
      "toggle-sidebar",
    ])
    expect(resolved[1]?.label).toBe("Current sidebar")
  })

  it("drops unavailable contextual commands", () => {
    const unavailable = { ...command("terminal"), available: false }
    expect(
      resolveAppCommands([], [{ key: 1, commands: [unavailable] }])
    ).toEqual([])
  })

  it("filters commands and thread results", () => {
    const cloud = {
      id: "cloud-1",
      title: "Fix cloud search",
    } as AgentThread
    const commands = [
      { ...command("settings", "Open settings"), aliases: ["preferences"] },
      { ...command("hidden", "Hidden"), showInPalette: false },
    ]

    const results = buildPaletteResults(commands, [cloud], "fix")

    expect(results.map((item) => item.id)).toEqual(["cloud:cloud-1"])
    expect(buildPaletteResults(commands, [], "preferences")[0]?.id).toBe(
      "command:settings"
    )
  })
})
