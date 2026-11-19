/** @vitest-environment jsdom */
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { api, type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"
import { RoutingSettings } from "./RoutingSettings"

vi.mock("@/lib/profile", () => ({
  useRepos: () => ({
    data: {
      repositories: ["acme/api", "acme/web", "acme/new"].map((full_name) => ({
        full_name,
        private: true,
      })),
    },
  }),
}))
vi.mock("@/lib/slack-channels", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/slack-channels")>()),
  useSlackChannelDirectory: () => ({
    data: {
      channels: [
        { id: "CTEAM01", name: "team" },
        { id: "COTHER1", name: "other" },
      ].map((channel) => ({
        ...channel,
        is_member: true,
        is_private: false,
        is_ext_shared: false,
        num_members: 4,
      })),
      refreshing: false,
    },
    isLoading: false,
    isError: false,
    isRefreshing: false,
    refresh: vi.fn(),
  }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

class RoutingSettingsServer {
  records: Array<WorkspaceOption> = [
    {
      slug: "default",
      name: "Default legacy name",
      repos: ["acme/api"],
      default_repo: null,
      slack_channel_ids: ["CTEAM01"],
      kitchen_channel_ids: ["CTEAM01"],
      ask_channel_ids: [],
      is_default: true,
      has_snapshot: false,
    },
    {
      slug: "oss",
      name: "OSS legacy name",
      repos: ["acme/web"],
      default_repo: null,
      slack_channel_ids: ["COTHER1"],
      kitchen_channel_ids: [],
      ask_channel_ids: ["COTHER1"],
      is_default: false,
      has_snapshot: false,
    },
  ]
  render() {
    const records = this.records
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      execution_defaults_enabled: false,
      default_slug: "default",
      workspaces: records,
    })
    vi.spyOn(api, "listWorkspaceRepositories").mockImplementation(
      async (slug) =>
        records
          .find((record) => record.slug === slug)!
          .repos.map((repo) => ({ repo, may_start_threads: false }))
    )
    const get = vi
      .spyOn(api, "getWorkspace")
      .mockImplementation(async (slug): Promise<WorkspaceRecord> => ({
        ...records.find((record) => record.slug === slug)!,
        prompt: "keep",
      }))
    const update = vi
      .spyOn(api, "updateWorkspace")
      .mockImplementation(async (slug, changes) => ({
        ...records.find((record) => record.slug === slug)!,
        prompt: "keep",
        ...changes,
      }))
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <RoutingSettings />
        </TooltipProvider>
      </QueryClientProvider>
    )
    return { records, get, update }
  }
}

it("keeps permission writes on the existing owner, locks concurrent changes, and rolls back failures", async () => {
  let rejectSave: (error: Error) => void = () => {}
  const configure = vi
    .spyOn(api, "configureWorkspaceRepository")
    .mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectSave = reject
        })
    )
  const server = new RoutingSettingsServer()
  server.render()
  const web = await screen.findByRole("switch", {
    name: "Let acme/web start threads",
  })
  await vi.waitFor(() => expect(web.hasAttribute("data-disabled")).toBe(false))
  expect(screen.queryByText("OSS legacy name")).toBeNull()
  fireEvent.click(web)
  await vi.waitFor(() =>
    expect(configure).toHaveBeenCalledWith("oss", "acme/web", {
      may_start_threads: true,
    })
  )
  const apiToggle = screen.getByRole("switch", {
    name: "Let acme/api start threads",
  })
  expect(web.getAttribute("aria-checked")).toBe("true")
  expect(web.hasAttribute("data-disabled")).toBe(true)
  expect(apiToggle.hasAttribute("data-disabled")).toBe(true)
  fireEvent.click(apiToggle)
  expect(configure).toHaveBeenCalledTimes(1)
  rejectSave(new Error("Permission save refused"))
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Permission save refused"
  )
  await vi.waitFor(() => expect(web.getAttribute("aria-checked")).toBe("false"))
})

it("adds routing only to the existing default record and preserves freshly added unrelated repositories", async () => {
  const server = new RoutingSettingsServer()
  const { get, update, records } = server.render()
  get.mockResolvedValue({
    ...records[0]!,
    prompt: "keep",
    repos: ["acme/api", "acme/concurrent"],
  })
  const choose = await screen.findByRole("button", {
    name: "Choose repositories",
  })
  await vi.waitFor(() => expect(choose.hasAttribute("disabled")).toBe(false))
  fireEvent.click(choose)
  fireEvent.click(await screen.findByRole("checkbox", { name: "new" }))
  fireEvent.click(screen.getByRole("button", { name: "Save 3 repositories" }))
  await vi.waitFor(() =>
    expect(update).toHaveBeenCalledWith("default", {
      repos: ["acme/api", "acme/concurrent", "acme/new"],
    })
  )
  expect(get).toHaveBeenCalledWith("default")
  expect(update).toHaveBeenCalledTimes(1)
})

it("removes only the chosen channel and its modes without losing fresh unrelated bindings", async () => {
  const server = new RoutingSettingsServer()
  const { get, update, records } = server.render()
  get.mockResolvedValue({
    ...records[0]!,
    prompt: "keep",
    slack_channel_ids: ["CTEAM01", "CCONCURRENT"],
    kitchen_channel_ids: ["CTEAM01", "CCONCURRENT"],
  })
  const choose = await screen.findByRole("button", { name: "Choose channels" })
  await vi.waitFor(() => expect(choose.hasAttribute("disabled")).toBe(false))
  fireEvent.click(choose)
  fireEvent.click(await screen.findByRole("checkbox", { name: "#team" }))
  fireEvent.click(screen.getByRole("button", { name: "Save 1 channel" }))
  await vi.waitFor(() =>
    expect(update).toHaveBeenCalledWith("default", {
      slack_channel_ids: ["CCONCURRENT"],
      kitchen_channel_ids: ["CCONCURRENT"],
      ask_channel_ids: [],
    })
  )
  expect(update).toHaveBeenCalledTimes(1)
})

it("changes a channel mode on its existing owner without changing other modes or bindings", async () => {
  const server = new RoutingSettingsServer()
  const { get, update, records } = server.render()
  get.mockResolvedValue({
    ...records[0]!,
    prompt: "keep",
    kitchen_channel_ids: ["CTEAM01", "CCONCURRENT"],
    ask_channel_ids: ["COTHERASK"],
  })
  const mode = await screen.findByRole("combobox", {
    name: "Channel type for #team",
  })
  fireEvent.click(mode)
  const ask = await screen.findByRole("option", { name: "Ask" })
  fireEvent.pointerDown(ask)
  fireEvent.click(ask)
  await vi.waitFor(() =>
    expect(update).toHaveBeenCalledWith("default", {
      kitchen_channel_ids: ["CCONCURRENT"],
      ask_channel_ids: ["COTHERASK", "CTEAM01"],
    })
  )
})
