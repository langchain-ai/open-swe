/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { SidebarFrame, useSidebarLayout } from "./sidebar-layout"

describe("responsive sidebar", () => {
  const originalWidth = window.innerWidth

  beforeEach(() => {
    window.localStorage.clear()
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1200,
    })
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: originalWidth,
    })
  })

  it("auto-collapses on narrow windows and restores the desktop preference", () => {
    const { result } = renderHook(() => useSidebarLayout())
    expect(result.current.collapsed).toBe(false)

    act(() => result.current.setCollapsed(false))
    act(() => {
      Object.defineProperty(window, "innerWidth", { value: 900 })
      window.dispatchEvent(new Event("resize"))
    })
    expect(result.current.collapsed).toBe(true)
    expect(window.localStorage.getItem("open-swe.sidebar.collapsed")).toBe("0")

    act(() => result.current.toggle())
    expect(result.current.collapsed).toBe(false)
    act(() => {
      Object.defineProperty(window, "innerWidth", { value: 850 })
      window.dispatchEvent(new Event("resize"))
    })
    expect(result.current.collapsed).toBe(false)

    act(() => {
      Object.defineProperty(window, "innerWidth", { value: 1200 })
      window.dispatchEvent(new Event("resize"))
    })
    expect(result.current.collapsed).toBe(false)
    act(() => {
      Object.defineProperty(window, "innerWidth", { value: 900 })
      window.dispatchEvent(new Event("resize"))
    })
    expect(result.current.collapsed).toBe(true)
  })

  it("keeps narrow-window toggles separate from a saved collapsed desktop", () => {
    window.localStorage.setItem("open-swe.sidebar.collapsed", "1")
    Object.defineProperty(window, "innerWidth", { value: 900 })
    const { result } = renderHook(() => useSidebarLayout())
    expect(result.current.collapsed).toBe(true)

    act(() => result.current.toggle())
    expect(result.current.collapsed).toBe(false)
    expect(window.localStorage.getItem("open-swe.sidebar.collapsed")).toBe("1")

    act(() => {
      Object.defineProperty(window, "innerWidth", { value: 1200 })
      window.dispatchEvent(new Event("resize"))
    })
    expect(result.current.collapsed).toBe(true)

    act(() => result.current.toggle())
    expect(result.current.collapsed).toBe(false)
    expect(window.localStorage.getItem("open-swe.sidebar.collapsed")).toBe("0")
  })
})

describe("desktop title-bar controls", () => {
  afterEach(() => {
    cleanup()
    Reflect.deleteProperty(window, "openSweDesktop")
    vi.restoreAllMocks()
  })

  // Electron applies app regions in DOM order; anywhere but the root layout's
  // last-child host, a draggable header can follow and swallow their clicks.
  it.each([
    [false, true],
    [true, true],
    [false, false],
    [true, false],
  ])(
    "render in the controls host (collapsed: %s, history: %s)",
    async (collapsed, historyControls) => {
      Object.defineProperty(window, "openSweDesktop", {
        configurable: true,
        value: {},
      })
      render(
        <TooltipProvider>
          <SidebarFrame
            width={260}
            setWidth={() => {}}
            collapsed={collapsed}
            historyControls={historyControls}
            toggle={() => {}}
          >
            <div data-desktop-drag-region="" />
          </SidebarFrame>
          <div data-desktop-window-controls="" />
        </TooltipProvider>
      )

      const toggle = await screen.findByRole("button", {
        name: collapsed ? "Expand sidebar" : "Collapse sidebar",
      })
      expect(
        toggle.closest("[data-desktop-window-controls] > [data-no-drag]")
      ).not.toBeNull()
      for (const name of ["Back", "Forward"]) {
        const button = screen.queryByRole("button", { name })
        expect(button?.closest("[data-no-drag]") ?? null).toBe(
          historyControls ? toggle.closest("[data-no-drag]") : null
        )
      }

      const back = vi.spyOn(window.history, "back").mockImplementation(() => {})
      const forward = vi
        .spyOn(window.history, "forward")
        .mockImplementation(() => {})
      await act(async () => {
        window.dispatchEvent(
          new KeyboardEvent("keydown", { key: "[", ctrlKey: true })
        )
      })
      expect(back).toHaveBeenCalledOnce()
      await act(async () => {
        window.dispatchEvent(
          new KeyboardEvent("keydown", { key: "]", ctrlKey: true })
        )
      })
      expect(forward).toHaveBeenCalledOnce()
    }
  )
})
