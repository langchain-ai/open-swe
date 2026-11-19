/** @vitest-environment jsdom */

import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { pluginsApi } from "@/features/plugins/lib/api"
import { SidebarNav } from "./SidebarNav"

const location = vi.hoisted(() => ({ pathname: "/agents" }))

beforeEach(() => {
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      disconnect() {}
    }
  )
})

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ref }: React.ComponentProps<"a">) => (
    <a ref={ref} href="/agents">
      {children}
    </a>
  ),
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location }),
  useRouter: () => ({ options: { parseSearch: () => ({}) } }),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  location.pathname = "/agents"
})

it("always shows core navigation regardless of saved visibility preferences", () => {
  const client = new QueryClient()
  client.setQueryData(["session"], null)
  const savedPrefs = window.localStorage.getItem(
    "open-swe.agents.sidebar-prefs"
  )
  render(
    <QueryClientProvider client={client}>
      <SidebarNav onNavigate={() => {}} />
    </QueryClientProvider>
  )
  for (const name of ["Skills", "Automations", "Pull Requests"]) {
    expect(screen.getByRole("link", { name })).toBeTruthy()
  }
  expect(screen.queryByRole("button", { name: "More" })).toBeNull()
  expect(window.localStorage.getItem("open-swe.agents.sidebar-prefs")).toBe(
    savedPrefs
  )
})

it("shows navigation when signed out rather than waiting for disabled apps", () => {
  const client = new QueryClient()
  client.setQueryData(["session"], null)
  render(
    <QueryClientProvider client={client}>
      <SidebarNav onNavigate={() => {}} />
    </QueryClientProvider>
  )
  expect(screen.getByRole("link", { name: "Skills" })).toBeTruthy()
  expect(screen.queryByRole("status", { name: "Loading apps" })).toBeNull()
})

it("shows loading feedback when a session enables the apps request", async () => {
  let release!: (value: Awaited<ReturnType<typeof pluginsApi.list>>) => void
  vi.spyOn(pluginsApi, "list").mockImplementation(
    () => new Promise((resolve) => (release = resolve))
  )
  const client = new QueryClient()
  client.setQueryData(["session"], null)
  render(
    <QueryClientProvider client={client}>
      <SidebarNav onNavigate={() => {}} />
    </QueryClientProvider>
  )
  await act(() => client.setQueryData(["session"], { login: "test" }))
  expect(
    await screen.findByRole("status", { name: "Loading apps" })
  ).toBeTruthy()
  await act(() => release([]))
  expect(screen.getByRole("link", { name: "Skills" })).toBeTruthy()
  await waitFor(() =>
    expect(screen.queryByRole("status", { name: "Loading apps" })).toBeNull()
  )
  vi.restoreAllMocks()
})
