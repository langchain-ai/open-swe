/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { Suspense, type ComponentType, type ReactNode } from "react"
import { afterEach, beforeAll, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import { Route } from "./review_.repositories.$owner"

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  createFileRoute: () => (options: { component: ComponentType }) => ({
    options,
    useParams: () => ({ owner: "acme" }),
  }),
}))
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    data: { login: "me", is_admin: true },
    isLoading: false,
  }),
}))
vi.mock("@/lib/profile", () => ({
  useRepos: () => ({
    data: {
      repositories: [
        { full_name: "acme/api", private: false },
        { full_name: "acme/web", private: false },
      ],
    },
    isLoading: false,
  }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

type LazyPage = ComponentType & { preload?: () => Promise<void> }
const Page = (Route as unknown as { options: { component: LazyPage } }).options
  .component

beforeAll(async () => {
  await Page.preload?.()
})

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Suspense>
        <Page />
      </Suspense>
    </QueryClientProvider>
  )
}

const toggle = (repo: string) =>
  screen.getByRole("switch", {
    name: `Run reviews automatically for ${repo}`,
  })

async function startToggle() {
  vi.spyOn(api, "listAutoReviewRepos").mockResolvedValue({
    repos: ["acme/web"],
  })
  const settle: {
    resolve: (value: { repos: Array<string> }) => void
    reject: (error: Error) => void
  } = { resolve: () => {}, reject: () => {} }
  vi.spyOn(api, "setAutoReviewRepo").mockImplementation(
    () =>
      new Promise((resolve, reject) => {
        settle.resolve = resolve
        settle.reject = reject
      })
  )
  renderPage()
  await waitFor(() =>
    expect(toggle("acme/web").getAttribute("aria-checked")).toBe("true")
  )
  fireEvent.click(toggle("acme/api"))
  await waitFor(() =>
    expect(toggle("acme/api").getAttribute("aria-checked")).toBe("true")
  )
  return settle
}

it("flips the switch before the save resolves and locks only that row", async () => {
  const settle = await startToggle()

  expect(api.setAutoReviewRepo).toHaveBeenCalledWith("acme/api", true)
  expect(toggle("acme/api").hasAttribute("data-disabled")).toBe(true)
  expect(toggle("acme/web").hasAttribute("data-disabled")).toBe(false)

  const saved = { repos: ["acme/api", "acme/web"] }
  vi.mocked(api.listAutoReviewRepos).mockResolvedValue(saved)
  settle.resolve(saved)
  await waitFor(() =>
    expect(toggle("acme/api").hasAttribute("data-disabled")).toBe(false)
  )
  expect(toggle("acme/api").getAttribute("aria-checked")).toBe("true")
})

it("turns the switch back off when the save fails", async () => {
  const settle = await startToggle()

  settle.reject(new Error("nope"))
  await waitFor(() =>
    expect(toggle("acme/api").getAttribute("aria-checked")).toBe("false")
  )
  expect(toggle("acme/web").getAttribute("aria-checked")).toBe("true")
})
