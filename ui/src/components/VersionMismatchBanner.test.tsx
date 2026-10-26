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
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { useUnsentDraft } from "@/lib/buildReload"

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
    "New changes are available. Reload to get the latest updates."
  )
  expect(screen.queryByRole("link", { name: "Details" })).toBeNull()
  expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy()
  expect(document.body.classList.contains("version-mismatch")).toBe(true)
  client.setQueryData(["session"], session("old"))
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull())
  expect(document.body.classList.contains("version-mismatch")).toBe(false)
  client.clear()
})

it("reloads a stale tab on its next page change, unless that would lose a draft or repeat", async () => {
  window.__OPEN_SWE_BUNDLE__ = { commit: "old", built_at: "2026-09-01" }
  const reload = vi.fn()
  vi.stubGlobal("location", { ...window.location, reload })
  const client = new QueryClient()
  client.setQueryData(["session"], {
    login: "alice",
    build_info: { backend: { commit: "new" }, dashboard: { served: true } },
  })
  const root = createRootRoute({
    component: () => (
      <>
        <VersionMismatchBanner />
        <Outlet />
      </>
    ),
  })
  const DraftHolder = () => {
    useUnsentDraft(true)
    return null
  }
  const route = (path: string, component = () => null) =>
    createRoute({ getParentRoute: () => root, path, component })
  const router = createRouter({
    history: createMemoryHistory({ initialEntries: ["/"] }),
    routeTree: root.addChildren([
      route("/", DraftHolder),
      route("/agents"),
      route("/review"),
      route("/admin"),
    ]),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByRole("status")

  await act(() => router.navigate({ to: "/agents" }))
  expect(reload).not.toHaveBeenCalled()
  await act(() => router.navigate({ to: "/review" }))
  expect(reload).toHaveBeenCalledTimes(1)
  // The reload served the same build; another would loop.
  await act(() => router.navigate({ to: "/admin" }))
  expect(reload).toHaveBeenCalledTimes(1)

  vi.unstubAllGlobals()
  window.sessionStorage.clear()
  client.clear()
})
