// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { expect, it, vi } from "vitest"

import { OnboardingDialog } from "./OnboardingDialog"

vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { slack_oauth_enabled: true } }),
}))
vi.mock("@/lib/profile", () => ({
  useProfile: () => ({ data: { default_model: "model" } }),
  useOptions: () => ({}),
  useSaveProfile: () => ({}),
}))

it("dismisses Slack onboarding across remounts when storage writes fail", async () => {
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => {
      throw new DOMException("Storage unavailable", "QuotaExceededError")
    },
  })
  const warning = vi.spyOn(console, "warn").mockImplementation(() => {})
  try {
    const first = render(<OnboardingDialog />)
    fireEvent.click(
      await screen.findByRole("button", { name: "Don't ask again" })
    )
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    first.unmount()
    const second = render(<OnboardingDialog />)
    expect(screen.queryByRole("dialog")).toBeNull()
    second.unmount()
  } finally {
    warning.mockRestore()
    vi.unstubAllGlobals()
  }
})
