/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { SidebarFrame } from "./sidebar-layout"

describe("desktop title-bar controls", () => {
  afterEach(() => {
    cleanup()
    Reflect.deleteProperty(window, "openSweDesktop")
  })

  // Electron applies app regions in DOM order; anywhere but the root layout's
  // last-child host, a draggable header can follow and swallow their clicks.
  it.each([false, true])(
    "render in the controls host as one no-drag cluster (collapsed: %s)",
    async (collapsed) => {
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
      for (const button of [
        toggle,
        screen.getByRole("button", { name: "Back" }),
        screen.getByRole("button", { name: "Forward" }),
      ]) {
        expect(
          button.closest("[data-desktop-window-controls] > [data-no-drag]")
        ).not.toBeNull()
      }
    }
  )
})
