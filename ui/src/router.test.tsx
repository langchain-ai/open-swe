/** @vitest-environment jsdom */

import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router"
import { afterEach, expect, it, vi } from "vitest"
import { getRouter } from "./router"

vi.mock("./routeTree.gen", async () => {
  const { createRootRoute, createRoute, Outlet, useParams } =
    await import("@tanstack/react-router")
  const { useLayoutEffect, useRef } = await import("react")
  const root = createRootRoute({ component: Outlet })
  function Thread() {
    const { threadId } = useParams({ strict: false })
    const ref = useRef<HTMLDivElement>(null)
    useLayoutEffect(() => {
      if (ref.current) ref.current.scrollTop = threadId === "first" ? 500 : 1700
    }, [threadId])
    return <div ref={ref} data-testid="transcript" />
  }
  return {
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: "/agents/$threadId",
        component: Thread,
      }),
    ]),
  }
})

vi.mock("@tanstack/react-router-ssr-query", () => ({
  setupRouterSsrQueryIntegration: () => {},
}))
vi.mock("./lib/perf/threadLoad", () => ({ onRouterNavigation: () => {} }))

afterEach(() => cleanup())

it("does not overwrite a thread's own initial position with another thread's offset", async () => {
  const router = getRouter()
  router.update({
    context: router.options.context,
    history: createMemoryHistory({ initialEntries: ["/agents/first"] }),
    isServer: false,
  })
  await router.load()
  render(<RouterProvider router={router} />)
  const el = await screen.findByTestId("transcript")
  expect(el.scrollTop).toBe(500)
  el.dispatchEvent(new Event("scroll"))
  await act(async () => {
    await router.navigate({
      to: "/agents/$threadId",
      params: { threadId: "second" },
    })
  })
  await waitFor(() => expect(el.scrollTop).toBe(1700))
  expect(router.state.location.pathname).toBe("/agents/second")
})
