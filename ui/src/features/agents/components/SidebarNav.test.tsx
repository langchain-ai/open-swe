/** @vitest-environment jsdom */

import { act, cleanup, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { pluginsApi } from "@/features/plugins/lib/api"
import { SidebarNav } from "./SidebarNav"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { pathname: "/agents" } }),
  useRouter: () => ({ options: { parseSearch: () => ({}) } }),
}))

afterEach(() => cleanup())

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
