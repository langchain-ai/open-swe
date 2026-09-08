/** @vitest-environment jsdom */

import { render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { DesktopUpdateChannel } from "@/desktop"
import { DesktopUpdatesSection } from "./DesktopUpdatesSection"

const originalDesktop = window.openSweDesktop

afterEach(() => {
  Object.defineProperty(window, "openSweDesktop", {
    configurable: true,
    value: originalDesktop,
  })
  vi.restoreAllMocks()
})

describe("DesktopUpdatesSection", () => {
  it("shows the persisted desktop update channel", async () => {
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value: {
        getUpdateChannel: vi
          .fn<() => Promise<DesktopUpdateChannel>>()
          .mockResolvedValue("nightly"),
      },
    })

    render(<DesktopUpdatesSection />)

    expect((await screen.findByRole("combobox")).textContent).toContain(
      "nightly"
    )
    expect(screen.getByText(/latest desktop builds/)).toBeTruthy()
  })

  it("stays hidden outside the desktop app", () => {
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value: undefined,
    })

    const view = render(<DesktopUpdatesSection />)

    expect(view.container.childElementCount).toBe(0)
  })
})
