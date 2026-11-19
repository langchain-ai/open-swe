/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  useSearch,
} from "@tanstack/react-router"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import {
  parsePullRequestSelection,
  validateReviewsSearch,
} from "@/features/reviews/search"
import { ContextNavigation } from "@/lib/contextNavigation"
import { useReviewQueue } from "./ReviewQueue"

vi.mock("./queries", () => ({ warmReviewPage: vi.fn() }))

afterEach(() => {
  cleanup()
  window.sessionStorage.clear()
})

function QueuePage() {
  const search = useSearch({ strict: false })
  const pr = parsePullRequestSelection(search.pr)
  const queue = useReviewQueue(pr ?? { owner: "acme", repo: "api", number: 1 })
  if (!queue) return <p>No queue</p>
  return (
    <>
      <p>
        {queue.position} of {queue.total}
      </p>
      <button onClick={queue.advance}>Next</button>
    </>
  )
}

class QueueScenario {
  static async render(queue?: string[]) {
    const root = createRootRoute()
    const page = createRoute({
      getParentRoute: () => root,
      path: "/agents/reviews/",
      validateSearch: validateReviewsSearch,
      component: QueuePage,
    })
    const router = createRouter({
      isServer: false,
      routeTree: root.addChildren([page]),
      history: createMemoryHistory({
        initialEntries: ["/agents/reviews?pr=acme%2Fapi%231&q=retry"],
      }),
    })
    ContextNavigation.for(router)
    await router.load()
    if (queue)
      await router.navigate({
        to: "/agents/reviews",
        search: { pr: "acme/api#1", q: "retry", queue },
      })
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    )
    return router
  }
}

it("does not enroll a directly opened PR in a review queue", async () => {
  await QueueScenario.render()
  await screen.findByText("No queue")
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull()
})

it("advances only through the explicitly supplied queue", async () => {
  const queue = ["acme/api#1", "acme/api#2"]
  const router = await QueueScenario.render(queue)
  await screen.findByText("1 of 2")
  fireEvent.click(screen.getByRole("button", { name: "Next" }))
  await waitFor(() =>
    expect(router.state.location.search.pr).toBe("acme/api#2")
  )
  await screen.findByText("2 of 2")
  expect(router.state.location.pathname).toBe("/agents/reviews")
  expect(router.state.location.search).toEqual({
    queue,
    pr: "acme/api#2",
    q: "retry",
  })
  fireEvent.click(screen.getByRole("button", { name: "Next" }))
  await screen.findByText("No queue")
  expect(router.state.location.search.pr).toBeUndefined()
  expect(router.state.location.search.q).toBe("retry")
})
