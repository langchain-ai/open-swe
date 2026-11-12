/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
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
  return client
}

const toggle = (repo: string) =>
  screen.getByRole("switch", {
    name: `Run reviews automatically for ${repo}`,
  })

async function startToggle(repo = "acme/api", on = true, enabledOnly = false) {
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
  if (enabledOnly)
    fireEvent.click(screen.getByRole("checkbox", { name: "Enabled only" }))
  fireEvent.click(toggle(repo))
  await waitFor(() =>
    expect(toggle(repo).getAttribute("aria-checked")).toBe(String(on))
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

it("keeps a pending disabled row visible until the save succeeds", async () => {
  const settle = await startToggle("acme/web", false, true)

  expect(toggle("acme/web").hasAttribute("data-disabled")).toBe(true)
  vi.mocked(api.listAutoReviewRepos).mockResolvedValue({ repos: [] })
  settle.resolve({ repos: [] })

  await waitFor(() =>
    expect(screen.queryByRole("switch", { name: /acme\/web/ })).toBeNull()
  )
  expect(
    screen.getByText("No repositories have automatic reviews enabled.")
  ).toBeTruthy()
})

it("keeps a failed disabled row visible and restores its enabled state", async () => {
  const settle = await startToggle("acme/web", false, true)

  expect(toggle("acme/web").hasAttribute("data-disabled")).toBe(true)
  settle.reject(new Error("nope"))

  await waitFor(() =>
    expect(toggle("acme/web").hasAttribute("data-disabled")).toBe(false)
  )
  expect(toggle("acme/web").getAttribute("aria-checked")).toBe("true")
})

it("filters cached enabled repositories after a background refresh fails", async () => {
  vi.spyOn(api, "listAutoReviewRepos").mockResolvedValue({
    repos: ["acme/web"],
  })
  const client = renderPage()
  await waitFor(() =>
    expect(toggle("acme/web").getAttribute("aria-checked")).toBe("true")
  )

  vi.mocked(api.listAutoReviewRepos).mockRejectedValue(
    new Error("refresh failed")
  )
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["autoReviewRepos"] })
  })
  expect(await screen.findByRole("alert")).toBeTruthy()
  expect(screen.getByText("1/2 run automatically")).toBeTruthy()
  expect(toggle("acme/web").getAttribute("aria-checked")).toBe("true")
  expect(toggle("acme/api").getAttribute("aria-checked")).toBe("false")

  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled only" }))
  expect(screen.queryByRole("switch", { name: /acme\/api/ })).toBeNull()
  expect(toggle("acme/web")).toBeTruthy()

  fireEvent.change(
    screen.getByRole("textbox", { name: "Search repositories" }),
    {
      target: { value: "api" },
    }
  )
  expect(
    screen.getByText("No enabled repositories match your search.")
  ).toBeTruthy()

  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled only" }))
  expect(toggle("acme/api")).toBeTruthy()
  expect(screen.queryByRole("switch", { name: /acme\/web/ })).toBeNull()
})

it("turns the switch back off when the save fails", async () => {
  const settle = await startToggle()

  settle.reject(new Error("nope"))
  await waitFor(() =>
    expect(toggle("acme/api").getAttribute("aria-checked")).toBe("false")
  )
  expect(toggle("acme/web").getAttribute("aria-checked")).toBe("true")
})

it("shows a settings error instead of an empty enabled list and recovers on retry", async () => {
  let rejectLoad: (error: Error) => void = () => {}
  vi.spyOn(api, "listAutoReviewRepos").mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        rejectLoad = reject
      })
  )
  renderPage()
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled only" }))
  rejectLoad(new Error("settings unavailable"))

  expect(await screen.findByRole("alert")).toBeTruthy()
  expect(
    screen.queryByText("No repositories have automatic reviews enabled.")
  ).toBeNull()
  expect(screen.queryByText("0/2 run automatically")).toBeNull()
  expect(screen.queryByRole("switch")).toBeNull()

  vi.mocked(api.listAutoReviewRepos).mockResolvedValue({ repos: ["acme/web"] })
  fireEvent.click(screen.getByRole("button", { name: "Retry" }))

  await waitFor(() =>
    expect(toggle("acme/web").getAttribute("aria-checked")).toBe("true")
  )
  expect(screen.queryByRole("alert")).toBeNull()
  expect(screen.queryByRole("switch", { name: /acme\/api/ })).toBeNull()
  expect(screen.getByText("1/2 run automatically")).toBeTruthy()
})
