/** @vitest-environment jsdom */

import { describe, expect, it, vi } from "vitest"
import { createElement } from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { AppCommandProvider, useRegisterAppCommands } from "./appCommands"

const { mockNavigate } = vi.hoisted(() => ({ mockNavigate: vi.fn() }))

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => mockNavigate }))
vi.mock("@/lib/session", () => ({ useSession: () => ({ data: {} }) }))
vi.mock("@/lib/theme", () => ({ useTheme: () => ({ toggleTheme: vi.fn() }) }))
vi.mock("@/components/AppShortcutReference", () => ({
  AppShortcutReference: ({ open }: { open: boolean }) =>
    open ? createElement("h2", null, "Keyboard shortcuts") : null,
}))
vi.mock("@/components/AppCommandPalette", async (original) => ({
  ...(await original<object>()),
  AppCommandPalette: ({ open }: { open: boolean }) =>
    open ? createElement("input", { "aria-label": "Search" }) : null,
}))

function CommandFixture({ commands }: { commands: ReadonlyArray<AppCommand> }) {
  useRegisterAppCommands(commands)
  return createElement("textarea", { "aria-label": "Composer" })
}

import {
  createNewThreadCommand,
  DesktopMenuCommands,
  resolveAppCommands,
} from "./appCommands"
import { eventMatchesShortcut } from "./hotkeys"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AppCommand } from "./appCommands"
import { buildPaletteResults } from "@/components/AppCommandPalette"

function command(id: string, label = id): AppCommand {
  return { id, label, group: "General", run: vi.fn() }
}

const modifier = () =>
  /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent)
    ? { metaKey: true }
    : { ctrlKey: true }

function renderCommands(commands: ReadonlyArray<AppCommand>) {
  return render(
    createElement(
      AppCommandProvider,
      null,
      createElement(CommandFixture, { commands })
    )
  )
}

describe("app commands", () => {
  it("only allows opted-in modifier commands from text fields", () => {
    const find = vi.fn()
    const navigate = vi.fn()
    const commands: AppCommand[] = [
      {
        ...command("conversation-find"),
        shortcuts: ["mod+f"],
        enableInFormFields: true,
        run: find,
      },
      { ...command("navigate"), shortcuts: ["mod+j"], run: navigate },
    ]
    const view = renderCommands(commands)
    const input = view.getByRole("textbox")
    fireEvent.keyDown(input, { key: "f", ...modifier() })
    fireEvent.keyDown(input, { key: "j", ...modifier() })
    expect(find).toHaveBeenCalledOnce()
    expect(navigate).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: "j", ...modifier() })
    expect(navigate).toHaveBeenCalledOnce()
    cleanup()
  })
  it("creates one new thread from the composer and the open palette", () => {
    const newThread = vi.fn()
    const view = renderCommands([createNewThreadCommand(newThread)])

    fireEvent.keyDown(view.getByRole("textbox"), { key: "n", ...modifier() })
    expect(newThread).toHaveBeenCalledOnce()

    fireEvent.keyDown(window, { key: "k", ...modifier() })
    const search = view.getByRole("textbox", { name: "Search" })
    fireEvent.keyDown(search, { key: "j" })
    fireEvent.keyDown(search, { key: "n", ...modifier() })
    expect(newThread).toHaveBeenCalledTimes(2)
    expect(view.queryByRole("textbox", { name: "Search" })).toBeNull()
    cleanup()
  })

  it("leaves shortcuts the desktop menu registered to it", async () => {
    const newThread = vi.fn()
    const pin = vi.fn()
    const setMenuCommands = vi.fn(async () => ["new-thread"])
    let runMenuCommand: (
      commandId: string,
      viaShortcut: boolean
    ) => void = () => undefined
    window.openSweDesktop = {
      isDesktop: true,
      onCommand: () => () => undefined,
      setMenuCommands,
      onMenuCommand: (
        callback: (commandId: string, viaShortcut: boolean) => void
      ) => {
        runMenuCommand = callback
        return () => undefined
      },
    } as unknown as NonNullable<typeof window.openSweDesktop>
    try {
      const view = renderCommands([
        createNewThreadCommand(newThread),
        {
          ...command("pin-thread", "Pin thread"),
          group: "Thread",
          shortcuts: ["mod+shift+p"],
          run: pin,
        },
      ])

      await act(async () => undefined)
      fireEvent.keyDown(view.getByRole("textbox"), {
        key: "n",
        ...modifier(),
      })
      expect(newThread).not.toHaveBeenCalled()
      view.getByRole("textbox").focus()
      act(() => runMenuCommand("new-thread", true))
      expect(newThread).toHaveBeenCalledOnce()
      act(() => runMenuCommand("search-commands", true))
      expect(view.queryByRole("textbox", { name: "Search" })).not.toBeNull()
      view.getByRole("textbox", { name: "Composer" }).focus()
      mockNavigate.mockClear()
      act(() => runMenuCommand("open-settings", true))
      expect(mockNavigate).toHaveBeenCalledWith({ to: "/my-settings" })
      act(() => runMenuCommand("show-keyboard-shortcuts", true))
      expect(
        view.queryByRole("heading", { name: "Keyboard shortcuts" })
      ).not.toBeNull()
      act(() => runMenuCommand("open-settings", false))
      view.getByRole("textbox", { name: "Composer" }).focus()
      fireEvent.keyDown(view.getByRole("textbox", { name: "Composer" }), {
        key: "?",
        shiftKey: true,
      })
      expect(
        view.queryByRole("heading", { name: "Keyboard shortcuts" })
      ).toBeNull()
      act(() => runMenuCommand("pin-thread", true))
      expect(pin).not.toHaveBeenCalled()
      act(() => runMenuCommand("pin-thread", false))
      expect(pin).toHaveBeenCalledOnce()
      view.getByRole("textbox").setAttribute("data-hotkeys", "ignore")
      act(() => runMenuCommand("new-thread", true))
      expect(newThread).toHaveBeenCalledOnce()
      view.getByRole("textbox").removeAttribute("data-hotkeys")
      view.getByRole("textbox").blur()
      fireEvent.keyDown(window, { key: "p", shiftKey: true, ...modifier() })
      expect(pin).toHaveBeenCalledTimes(2)
      expect(setMenuCommands).toHaveBeenLastCalledWith(
        expect.arrayContaining([
          {
            id: "new-thread",
            label: "New Thread",
            group: "General",
            menu: "file",
            shortcut: "mod+n",
          },
          {
            id: "pin-thread",
            label: "Pin Thread",
            group: "Thread",
            menu: "thread",
            shortcut: "mod+shift+p",
          },
        ])
      )
    } finally {
      cleanup()
      delete window.openSweDesktop
    }
  })

  it("title-cases menu labels and skips commands that only document keys", () => {
    expect(
      DesktopMenuCommands.entries([
        { ...command("ask", "Show or hide the file list"), shortcuts: ["f"] },
        { ...command("send", "Send message"), run: undefined },
      ])
    ).toEqual([
      {
        id: "ask",
        label: "Show or Hide the File List",
        group: "General",
        menu: "file",
      },
    ])
  })

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
