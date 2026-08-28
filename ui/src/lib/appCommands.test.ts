/** @vitest-environment jsdom */

import { describe, expect, it, vi } from "vitest"

import {
  createNewThreadCommand,
  resolveAppCommands,
  resolveKeyboardCommand,
} from "./appCommands"
import type { DesktopLocalThreadSummary } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AppCommand } from "./appCommands"
import { buildPaletteResults } from "@/components/AppCommandPalette"

function command(id: string, label = id): AppCommand {
  return { id, label, group: "General", run: vi.fn() }
}

describe("app commands", () => {
  it("uses Linear-style C for new threads on web and desktop", () => {
    const newThread = createNewThreadCommand(vi.fn())

    expect(newThread.shortcuts).toEqual(["c"])
    expect(newThread.desktopShortcuts).toBeUndefined()
    expect(newThread.desktopId).toBe("new-thread")
  })

  it("allows opted-in shortcuts in form fields without enabling single-key commands", () => {
    const input = document.createElement("input")
    const palette = {
      ...command("search-commands"),
      shortcuts: ["mod+k"],
      alwaysAvailable: true,
    }
    const newThread = createNewThreadCommand(vi.fn())
    const paletteEvent = new KeyboardEvent("keydown", {
      key: "k",
      ctrlKey: true,
    })
    Object.defineProperty(paletteEvent, "target", { value: input })
    const newThreadEvent = new KeyboardEvent("keydown", { key: "c" })
    Object.defineProperty(newThreadEvent, "target", { value: input })

    expect(
      resolveKeyboardCommand([palette, newThread], paletteEvent, false)
    ).toBe(palette)
    expect(
      resolveKeyboardCommand([palette, newThread], newThreadEvent, false)
    ).toBeUndefined()
    const composingEvent = new KeyboardEvent("keydown", {
      key: "k",
      ctrlKey: true,
      isComposing: true,
    })
    Object.defineProperty(composingEvent, "target", { value: input })
    expect(
      resolveKeyboardCommand([palette, newThread], composingEvent, false)
    ).toBeUndefined()
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

  it("filters commands and merges cloud and local thread results", () => {
    const cloud = {
      id: "cloud-1",
      title: "Fix cloud search",
    } as AgentThread
    const local: DesktopLocalThreadSummary = {
      id: "local-1",
      title: "Fix local search",
      cwd: "/tmp/repo",
      viewed: true,
      createdAt: 1,
      updatedAt: 2,
      modelId: null,
      effort: null,
    }
    const commands = [
      { ...command("settings", "Open settings"), aliases: ["preferences"] },
      { ...command("hidden", "Hidden"), showInPalette: false },
    ]

    const results = buildPaletteResults(commands, [cloud], [local], "fix")

    expect(results.map((item) => item.id)).toEqual([
      "cloud:cloud-1",
      "local:local-1",
    ])
    expect(buildPaletteResults(commands, [], [], "preferences")[0]?.id).toBe(
      "command:settings"
    )
  })
})
