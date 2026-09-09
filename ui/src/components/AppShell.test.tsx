/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SettingsSection } from "./AppShell"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock("@/components/AppSidebar", () => ({ AppSidebar: () => null }))

afterEach(() => {
  cleanup()
  window.history.replaceState(null, "", "/")
  vi.restoreAllMocks()
})

describe("SettingsSection", () => {
  it("links its heading and scrolls to an initial hash", () => {
    window.history.replaceState(null, "", "/my-settings#pull-requests")
    const scrollIntoView = vi.fn()
    HTMLElement.prototype.scrollIntoView = scrollIntoView

    render(
      <SettingsSection title="Pull Requests">
        <div>Settings</div>
      </SettingsSection>
    )

    const heading = screen.getByRole("heading", { name: "Pull Requests" })
    const section = heading.closest("section")
    expect(section?.id).toBe("pull-requests")
    expect(
      screen.getByRole("link", { name: "Pull Requests" }).getAttribute("href")
    ).toBe("#pull-requests")
    expect(scrollIntoView).toHaveBeenCalledOnce()
  })
})
