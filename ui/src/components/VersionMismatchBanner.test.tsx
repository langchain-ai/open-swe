/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { VersionMismatchBanner } from "./VersionMismatchBanner"

afterEach(() => {
  cleanup()
  delete window.__OPEN_SWE_BUNDLE__
  vi.unstubAllGlobals()
})

it("waits for the web bundle to publish independently of the backend and clears on convergence", async () => {
  window.__OPEN_SWE_BUNDLE__ = { commit: "old", built_at: "2026-09-01" }
  let published = "old"
  const fetchBuild = vi.fn(async () => Response.json({ commit: published }))
  vi.stubGlobal("fetch", fetchBuild)
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
  await waitFor(() => expect(fetchBuild).toHaveBeenCalledOnce())
  client.setQueryData(["session"], session("new"))
  await client.refetchQueries({ queryKey: ["dashboard-build"] })
  expect(screen.queryByRole("status")).toBeNull()
  published = "new"
  await client.refetchQueries({ queryKey: ["dashboard-build"] })
  expect((await screen.findByRole("status")).textContent).toContain(
    "New changes are available. Reload to get the latest updates."
  )
  expect(screen.queryByRole("link", { name: "Details" })).toBeNull()
  expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy()
  expect(document.body.classList.contains("version-mismatch")).toBe(true)
  window.__OPEN_SWE_BUNDLE__.commit = "new"
  client.setQueryData(["session"], session("old"))
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull())
  expect(document.body.classList.contains("version-mismatch")).toBe(false)
  client.clear()
})
