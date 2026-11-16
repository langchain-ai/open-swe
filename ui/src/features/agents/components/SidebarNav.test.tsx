/** @vitest-environment jsdom */

import { act, cleanup, render, screen } from "@testing-library/react"
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
    <a ref={ref}>{children}</a>
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

it("temporarily shows an active hidden item", () => {
  const client = new QueryClient()
  client.setQueryData(["session"], null)
  const savedPrefs = window.localStorage.getItem(
    "open-swe.agents.sidebar-prefs"
  )
  const view = (
    <QueryClientProvider client={client}>
      <SidebarNav onNavigate={() => {}} />
    </QueryClientProvider>
  )
  const { rerender } = render(view)
  expect(screen.queryByText("Skills")).toBeNull()

  location.pathname = "/agents/skills/new"
  rerender(
    <QueryClientProvider client={client}>
      <SidebarNav onNavigate={() => {}} />
    </QueryClientProvider>
  )
  expect(screen.getByText("Skills")).toBeTruthy()
  expect(window.localStorage.getItem("open-swe.agents.sidebar-prefs")).toBe(
    savedPrefs
  )

  location.pathname = "/agents"
  rerender(
    <QueryClientProvider client={client}>
      <SidebarNav onNavigate={() => {}} />
    </QueryClientProvider>
  )
  expect(screen.queryByText("Skills")).toBeNull()
})

it("shows navigation when signed out rather than waiting for disabled apps", () => {
  const client = new QueryClient()
  client.setQueryData(["session"], null)
  render(
    <QueryClientProvider client={client}>
      <SidebarNav onNavigate={() => {}} />
    </QueryClientProvider>
  )
  expect(screen.getByRole("button", { name: "More" })).toBeTruthy()
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
  expect(await screen.findByRole("button", { name: "More" })).toBeTruthy()
  vi.restoreAllMocks()
})
