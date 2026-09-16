/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { OnboardingDialog } from "./OnboardingDialog"

const identity = vi.hoisted(() => ({ login: "alice" }))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: identity.login } }),
}))
vi.mock("@/lib/api", () => ({
  api: { myMapping: async () => ({}) },
  connectService: vi.fn(),
}))
vi.mock("@/lib/profile", () => ({
  useProfile: () => ({ data: {}, isLoading: false }),
  useOptions: () => ({ data: { models: [] } }),
  useSaveProfile: () => ({ mutateAsync: vi.fn() }),
  buildProfileUpdate: vi.fn(),
}))

beforeEach(() => {
  window.sessionStorage.clear()
  identity.login = "alice"
})
afterEach(cleanup)

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <OnboardingDialog />
    </QueryClientProvider>
  )
}

it("keeps Maybe later dismissed across navigation without dismissing another user's onboarding", () => {
  const first = mount()
  fireEvent.click(screen.getByRole("button", { name: "Maybe later" }))
  first.unmount()
  const second = mount()
  expect(screen.queryByRole("dialog")).toBeNull()
  second.unmount()
  identity.login = "bob"
  mount()
  expect(screen.getByRole("dialog")).toBeTruthy()
})

it("remembers dismissal with Escape", () => {
  const first = mount()
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
  first.unmount()
  mount()
  expect(screen.queryByRole("dialog")).toBeNull()
})
