/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it } from "vitest"

import { VersionMismatchBanner } from "./VersionMismatchBanner"

afterEach(() => {
  cleanup()
  delete window.__OPEN_SWE_BUNDLE__
})

it("warns about a lagging frontend even when it matches the backend-served bundle, and clears on convergence", async () => {
  window.__OPEN_SWE_BUNDLE__ = { commit: "old", built_at: "2026-09-01" }
  const client = new QueryClient()
  const session = (commit: string | null) => ({
    login: "alice",
    build_info: {
      backend: { commit },
      dashboard: { commit: "old", served: true },
    },
  })
  client.setQueryData(["session"], session(null))
  const router = createRouter({
    history: createMemoryHistory(),
    routeTree: createRootRoute({ component: VersionMismatchBanner }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  expect(screen.queryByRole("status")).toBeNull()
  client.setQueryData(["session"], session("new"))
  expect((await screen.findByRole("status")).textContent).toContain(
    "Frontend version differs from the deployed backend."
  )
  expect(
    screen.getByRole("link", { name: "Details" }).getAttribute("href")
  ).toBe("/my-settings/about")
  client.setQueryData(["session"], session("old"))
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull())
  client.clear()
})
