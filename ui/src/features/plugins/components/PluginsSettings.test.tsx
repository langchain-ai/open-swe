/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { PluginsSettings } from "./PluginsSettings"
import {
  pluginsApi,
  type App,
  type InstalledPlugin,
} from "@/features/plugins/lib/api"
import { reportError } from "@/lib/errorReporting"
import { makeQueryClient } from "@/lib/query"

vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))

class AppsSettingsScreen {
  readonly client = makeQueryClient()
  readonly version = {
    id: "release-8",
    number: 8,
    manifest: {
      name: "design-qa",
      displayName: "Design QA",
      version: "1.0.0",
      description:
        "Compare the saved before and after screenshots of your pull requests.",
      sidebar: { label: "Design QA", icon: "images" },
      permissions: [],
    },
    origin: { thread: "build-thread" },
    preview: false,
    created_at: null,
  } satisfies InstalledPlugin["version"]
  readonly app: App = {
    id: "app-id",
    name: "design-qa",
    mine: false,
    listed: true,
    latest: this.version,
  }
  readonly installation: InstalledPlugin = {
    id: "installation-id",
    plugin_id: this.app.id,
    name: this.app.name,
    manifest: this.version.manifest,
    version: this.version,
    mine: false,
    repositories: [],
    installed_at: null,
    featured: false,
  }

  async render() {
    this.client.setQueryData(["session"], { login: "person" })
    this.client.setDefaultOptions({ queries: { retry: false } })
    vi.spyOn(pluginsApi, "apps").mockResolvedValue([this.app])
    vi.spyOn(pluginsApi, "list").mockResolvedValue([this.installation])
    const router = createRouter({
      history: createMemoryHistory(),
      routeTree: createRootRoute({ component: PluginsSettings }),
    })
    await router.load()
    render(
      <QueryClientProvider client={this.client}>
        <TooltipProvider>
          <RouterProvider router={router} />
        </TooltipProvider>
      </QueryClientProvider>
    )
    await this.catalog.findByText("Built in a thread")
  }

  get catalog() {
    const section = screen
      .getByRole("heading", { name: "Apps" })
      .closest("section")
    if (!section) throw new Error("Apps section is missing")
    return within(section)
  }
}

beforeEach(() => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("removes a personally added listed app from the catalog and sidebar immediately", async () => {
  const page = new AppsSettingsScreen()
  const request = Promise.withResolvers<void>()
  const uninstall = vi
    .spyOn(pluginsApi, "uninstall")
    .mockReturnValue(request.promise)
  await page.render()

  fireEvent.click(
    page.catalog.getByRole("button", { name: "Remove Design QA" })
  )

  expect(await page.catalog.findByRole("button", { name: "Add" })).toBeTruthy()
  expect(screen.queryByRole("link", { name: "Design QA" })).toBeNull()
  expect(uninstall).toHaveBeenCalledExactlyOnceWith(page.installation.id)
  vi.mocked(pluginsApi.list).mockResolvedValue([])
  request.resolve()
  await waitFor(() => expect(page.client.isMutating()).toBe(0))
  expect(page.catalog.getByRole("button", { name: "Add" })).toBeTruthy()
  expect(page.catalog.getByText("Design QA")).toBeTruthy()
  page.client.clear()
})

it("restores both removal actions and reports the error when removal is rejected", async () => {
  const page = new AppsSettingsScreen()
  const request = Promise.withResolvers<void>()
  vi.spyOn(pluginsApi, "uninstall").mockReturnValue(request.promise)
  await page.render()
  fireEvent.click(
    page.catalog.getByRole("button", { name: "Remove Design QA" })
  )
  await page.catalog.findByRole("button", { name: "Add" })

  const error = new Error(
    "An admin added this app for everyone, so it can't be removed"
  )
  request.reject(error)

  await page.catalog.findByRole("button", { name: "Remove Design QA" })
  expect(screen.getByRole("link", { name: "Design QA" })).toBeTruthy()
  expect(
    screen.getAllByRole("button", { name: "Remove Design QA" })
  ).toHaveLength(2)
  expect(reportError).toHaveBeenCalledWith(
    expect.objectContaining({
      title: "Couldn't remove app",
      error,
      showToast: true,
    })
  )
  await waitFor(() => expect(page.client.isMutating()).toBe(0))
  page.client.clear()
})

it("explains why admin-managed apps cannot be removed in either list", async () => {
  const page = new AppsSettingsScreen()
  page.installation.featured = true
  await page.render()

  expect(page.catalog.getByText("Added for everyone")).toBeTruthy()
  expect(
    screen.getAllByText("An admin manages this app, so it can't be removed.")
  ).toHaveLength(2)
  expect(screen.queryByRole("button", { name: "Remove Design QA" })).toBeNull()
  expect(page.catalog.queryByRole("button", { name: "Add" })).toBeNull()
  page.client.clear()
})
