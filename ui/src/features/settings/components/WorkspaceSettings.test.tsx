/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  api,
  ApiError,
  type TeamSettings,
  type WorkspaceRecord,
} from "@/lib/api"

import { WorkspaceSettings } from "./WorkspaceSettings"

const RECORD: WorkspaceRecord = {
  slug: "oss",
  name: "OSS",
  prompt: "Run make test.",
  repos: ["acme/oss"],
  slack_channel_ids: ["C1"],
  setup_script: "make setup",
  update_script: "",
  base_snapshot_id: null,
  snapshot_status: "ready",
  refresh_status: "success",
  vcpus: 4,
  mem_bytes: 8 * 1024 ** 3,
  fs_capacity_bytes: null,
}

const SETTINGS: TeamSettings = {
  review_draft_prs: false,
  pr_summaries: true,
  review_trace_links: true,
}

const clients: Array<QueryClient> = []

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.restoreAllMocks()
})

function mockApis(record: WorkspaceRecord = RECORD) {
  vi.spyOn(api, "getWorkspace").mockResolvedValue(record)
  vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
    default_slug: "oss",
    workspaces: [
      {
        slug: "oss",
        name: "OSS",
        repos: ["acme/oss"],
        slack_channel_ids: ["C1"],
        is_default: true,
        has_snapshot: true,
      },
      {
        slug: "core",
        name: "Core",
        repos: ["acme/api"],
        slack_channel_ids: [],
        is_default: false,
        has_snapshot: false,
      },
    ],
  })
  vi.spyOn(api, "options").mockResolvedValue({
    models: [],
    default_agent_model: "anthropic:claude-opus-5",
    default_agent_reasoning_effort: "medium",
    default_agent_subagent_model: "anthropic:claude-opus-5",
    default_agent_subagent_reasoning_effort: "medium",
  })
  vi.spyOn(api, "getTeamSettings").mockResolvedValue(SETTINGS)
  vi.spyOn(api, "listSlackChannels").mockResolvedValue({
    channels: [
      {
        id: "C1",
        name: "oss-help",
        is_private: false,
        is_member: true,
        is_ext_shared: false,
        num_members: 3,
      },
    ],
    partial: false,
  })
  vi.spyOn(api, "getWorkspaceMCPs").mockResolvedValue([])
  vi.spyOn(api, "me").mockRejectedValue(new Error("not signed in"))
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceSettings slug="oss" canEdit />
    </QueryClientProvider>
  )
}

describe("WorkspaceSettings", () => {
  it("loads the record into the general form and saves the edited name", async () => {
    mockApis()
    const update = vi
      .spyOn(api, "updateWorkspace")
      .mockResolvedValue({ ...RECORD, name: "OSS support" })
    renderPage()

    const name = await screen.findByLabelText("Workspace name")
    expect((name as HTMLInputElement).value).toBe("OSS")
    expect(
      ((await screen.findByLabelText("Instructions")) as HTMLTextAreaElement)
        .value
    ).toBe("Run make test.")
    // Bound channels read by name once the directory is in.
    expect((await screen.findAllByText("#oss-help")).length).toBeGreaterThan(0)

    fireEvent.change(name, { target: { value: " OSS support " } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        name: "OSS support",
        repos: ["acme/oss"],
        slack_channel_ids: ["C1"],
        prompt: "Run make test.",
      })
    )
  })

  it("shows a save conflict in the alert region", async () => {
    mockApis()
    vi.spyOn(api, "updateWorkspace").mockRejectedValue(
      new ApiError(409, "repository acme/api already belongs to workspace core")
    )
    renderPage()

    fireEvent.change(await screen.findByLabelText("Workspace name"), {
      target: { value: "Renamed" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    expect((await screen.findByRole("alert")).textContent).toContain(
      "already belongs to workspace core"
    )
  })

  it("saves the sandbox scripts and starts a rebuild", async () => {
    mockApis()
    const update = vi.spyOn(api, "updateWorkspace").mockResolvedValue({
      ...RECORD,
      setup_script: "make setup && make build",
    })
    const refresh = vi
      .spyOn(api, "refreshWorkspace")
      .mockResolvedValue({ started: true, run_id: "run-1" })
    renderPage()

    const setup = await screen.findByLabelText("Setup script")
    expect((setup as HTMLTextAreaElement).value).toBe("make setup")
    fireEvent.change(setup, { target: { value: "make setup && make build" } })
    fireEvent.click(screen.getByRole("button", { name: "Save scripts" }))
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        setup_script: "make setup && make build",
        update_script: "",
      })
    )

    fireEvent.click(screen.getByRole("button", { name: "Rebuild image" }))
    await waitFor(() => expect(refresh).toHaveBeenCalledWith("oss"))
    expect(await screen.findByText(/Rebuild started/)).toBeTruthy()
  })

  it("offers only the workspace's own repositories as its default", async () => {
    mockApis()
    renderPage()

    // The selector stays disabled until the workspace's settings have loaded.
    const trigger = await screen.findByRole("button", {
      name: /Pick a repository/,
    })
    await waitFor(() => expect(trigger.hasAttribute("disabled")).toBe(false))
    fireEvent.click(trigger)

    // The chip in General plus the option in the dropdown; Core's repo nowhere.
    expect(screen.getAllByText("acme/oss").length).toBeGreaterThan(1)
    expect(screen.queryByText("acme/api")).toBeNull()
  })
})
