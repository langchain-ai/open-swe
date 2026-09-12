/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import {
  createRootRoute,
  createRouter,
  createMemoryHistory,
  RouterProvider,
  Outlet,
} from "@tanstack/react-router"
import { afterEach, expect, it, vi } from "vitest"
import { api, type ReviewListPayload } from "@/lib/api"
import { Route } from "@/routes/agents/reviews/index"

vi.mock("@/lib/api", () => ({ api: { listReviews: vi.fn() } }))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "octocat" } }),
}))
vi.mock("./MyPullRequests", () => ({
  MyPullRequests: () => <div>Open PRs</div>,
}))

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it("shows immediate progress and prevents double pagination during a slow request", async () => {
  const root = createRootRoute({ component: Outlet })
  const pageRoute = Route.update({
    getParentRoute: () => root,
    path: "/agents/reviews/",
  } as never)
  const router = createRouter({
    isServer: false,
    routeTree: root.addChildren([pageRoute]),
    history: createMemoryHistory({
      initialEntries: ["/agents/reviews/?tab=all"],
    }),
  })
  vi.mocked(api.listReviews).mockResolvedValueOnce({
    reviews: [],
    page: 0,
    has_more: true,
  })
  let resolve!: (value: ReviewListPayload) => void
  vi.mocked(api.listReviews).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done
      })
  )
  await router.load()
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByText("Page 1")
  fireEvent.click(screen.getByRole("button", { name: "Next" }))
  await screen.findByRole("status")
  expect(router.state.location.search).toMatchObject({ tab: "all", page: 1 })
  expect(screen.getByRole("status").textContent).toBe("Loading page 2…")
  expect(
    (screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled
  ).toBe(true)
  expect(
    (screen.getByRole("button", { name: "Prev" }) as HTMLButtonElement).disabled
  ).toBe(true)
  resolve({ reviews: [], page: 1, has_more: false })
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull())
  expect(
    (screen.getByRole("button", { name: "Prev" }) as HTMLButtonElement).disabled
  ).toBe(false)
})
