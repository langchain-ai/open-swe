/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router"
import { act, render, waitFor } from "@testing-library/react"
import { expect, it, vi } from "vitest"

import { AnalyticsIdentity } from "./AnalyticsIdentity"
import { productAnalytics } from "./analytics"
import type { SessionUser } from "./api"

vi.mock("./analytics", () => ({
  productAnalytics: { identify: vi.fn(), reset: vi.fn(), page: vi.fn() },
}))

it("tracks initial and SPA navigation with route templates, not query changes or refetches", async () => {
  const session: SessionUser = {
    login: "alice",
    user_id: "alice-uuid",
    email: null,
    avatar_url: null,
    is_admin: false,
  }
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity } },
  })
  queryClient.setQueryData(["session"], session)
  const root = createRootRoute({
    component: () => (
      <>
        <AnalyticsIdentity />
        <Outlet />
      </>
    ),
  })
  const thread = createRoute({
    getParentRoute: () => root,
    path: "/agents/$threadId",
  })
  const usage = createRoute({ getParentRoute: () => root, path: "/usage" })
  const router = createRouter({
    routeTree: root.addChildren([thread, usage]),
    history: createMemoryHistory({
      initialEntries: ["/agents/private-thread?secret=value"],
    }),
  })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await waitFor(() =>
    expect(productAnalytics.page).toHaveBeenCalledWith(
      "/agents/private-thread",
      "/agents/$threadId"
    )
  )
  await act(async () => {
    queryClient.setQueryData(["session"], {
      ...session,
      display_name: "Updated",
    })
  })
  expect(productAnalytics.page).toHaveBeenCalledTimes(1)
  await act(async () => {
    await router.navigate({
      to: "/agents/$threadId",
      params: { threadId: "private-thread" },
      search: { subagent: "other" },
      resetScroll: false,
    })
  })
  expect(productAnalytics.page).toHaveBeenCalledTimes(1)
  await act(async () => {
    await router.navigate({
      to: "/usage",
      search: { period: "30d" },
      resetScroll: false,
    })
  })
  await waitFor(() =>
    expect(productAnalytics.page).toHaveBeenLastCalledWith("/usage", "/usage")
  )
  expect(productAnalytics.page).toHaveBeenCalledTimes(2)
  await act(async () => {
    queryClient.setQueryData(["session"], null)
  })
  await waitFor(() => expect(productAnalytics.reset).toHaveBeenCalledOnce())
  queryClient.clear()
})
