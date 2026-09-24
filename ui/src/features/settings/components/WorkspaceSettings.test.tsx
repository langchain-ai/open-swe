/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  api,
  ApiError,
  type WorkspaceSettings,
  type WorkspaceRecord,
} from "@/lib/api"

import { WorkspaceSettingsPanel } from "./WorkspaceSettings"

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

const SETTINGS: WorkspaceSettings = {
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
        default_repo: null,
        has_snapshot: true,
      },
      {
        slug: "core",
        name: "Core",
        repos: ["acme/api"],
        slack_channel_ids: [],
        is_default: false,
        default_repo: null,
        has_snapshot: false,
      },
    ],
  })
  vi.spyOn(api, "options").mockResolvedValue({
    models: [],
    default_agent_model: "anthropic:claude-opus-5-5",
    default_agent_reasoning_effort: "medium",
    default_agent_subagent_model: "anthropic:claude-opus-5-5",
    default_agent_subagent_reasoning_effort: "medium",
  })
  vi.spyOn(api, "getWorkspaceSettings").mockResolvedValue({
    effective: SETTINGS,
    overrides: {},
  })
  vi.spyOn(api, "getInstanceMCPs").mockResolvedValue([])
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

function renderPage(canEdit = true, onDeleted = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceSettingsPanel
        slug="oss"
        canEdit={canEdit}
        onDeleted={onDeleted}
      />
    </QueryClientProvider>
  )
}

describe("WorkspaceSettingsPanel", () => {
  it("confirms deletion, keeps failures retryable, and leaves the detail page on success", async () => {
    mockApis()
    const onDeleted = vi.fn()
    const remove = vi
      .spyOn(api, "deleteWorkspace")
      .mockRejectedValueOnce(new Error("Could not delete the workspace"))
    renderPage(true, onDeleted)

    fireEvent.click(await screen.findByRole("button", { name: "Delete OSS" }))
    expect(screen.getByRole("alertdialog").textContent).toContain(
      "cannot be undone"
    )
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Cancel",
      })
    )
    expect(remove).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Delete OSS" }))
    fireEvent.click(screen.getByRole("button", { name: "Delete workspace" }))
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not delete the workspace"
    )
    expect(remove).toHaveBeenCalledWith("oss", expect.anything())
    expect(onDeleted).not.toHaveBeenCalled()

    let finish!: () => void
    remove.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    fireEvent.click(screen.getByRole("button", { name: "Delete workspace" }))
    expect(
      (await screen.findByRole("button", { name: "Deleting…" })).hasAttribute(
        "disabled"
      )
    ).toBe(true)
    expect(
      within(screen.getByRole("alertdialog"))
        .getByRole("button", { name: "Cancel" })
        .hasAttribute("disabled")
    ).toBe(true)
    finish()
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce())
    expect(screen.queryByRole("alertdialog")).toBeNull()
  })

  it("does not offer deletion without admin access", async () => {
    mockApis()
    renderPage(false)
    await screen.findByLabelText("Workspace name")
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull()
  })

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
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Workspace name") as HTMLInputElement).value
      ).toBe("OSS support")
    )
    expect(screen.queryByRole("status")).toBeNull()
  })

  it.each([
    ["refreshing", "success"],
    ["refreshing", "failed"],
    ["success", "success"],
    ["success", "failed"],
    ["refreshing", "unknown"],
    ["success", "unknown"],
  ] as const)(
    "follows a repository rebuild from a %s save response through stale polls to %s",
    async (savedStatus, outcome) => {
      const initial = {
        ...RECORD,
        refresh_finished_at: "2026-01-01T00:00:00Z",
      }
      mockApis(initial)
      vi.spyOn(api, "repos").mockResolvedValue({
        installations: [],
        repositories: [],
      })
      const saved = {
        ...initial,
        repos: [],
        refresh_finished_at: "2026-01-01T00:00:30Z",
      }
      vi.spyOn(api, "updateWorkspace").mockResolvedValue({
        ...saved,
        refresh_status: savedStatus,
      })
      renderPage()

      fireEvent.click(
        await screen.findByRole("button", { name: "Choose repositories" })
      )
      fireEvent.click(await screen.findByRole("checkbox", { name: "acme/oss" }))
      fireEvent.click(
        screen.getByRole("button", { name: "Save 0 repositories" })
      )
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())

      vi.useFakeTimers({
        toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
      })
      try {
        await act(async () => {
          fireEvent.click(screen.getByRole("button", { name: "Save" }))
          await vi.advanceTimersByTimeAsync(1)
        })
        const general = screen
          .getByRole("button", { name: "Save" })
          .closest("section")
        if (!general) throw new Error("no General section")
        expect(within(general).getByRole("status").textContent).toContain(
          savedStatus === "refreshing"
            ? "Rebuilding sandbox image"
            : "rebuild queued"
        )
        expect(
          screen
            .getByRole("button", {
              name:
                savedStatus === "refreshing" ? "Rebuilding…" : "Rebuild image",
            })
            .hasAttribute("disabled")
        ).toBe(savedStatus === "refreshing")

        const getWorkspace = vi
          .mocked(api.getWorkspace)
          .mockResolvedValue(saved)
        const reads = getWorkspace.mock.calls.length
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5001)
        })
        expect(getWorkspace.mock.calls.length).toBeGreaterThan(reads)
        expect(screen.getByRole("status").textContent).toContain(
          "rebuild queued"
        )

        getWorkspace.mockResolvedValue({
          ...saved,
          refresh_status: outcome === "unknown" ? "success" : "refreshing",
        })
        await act(async () => {
          await vi.advanceTimersByTimeAsync(65_001)
        })
        expect(
          within(general).getByRole(outcome === "unknown" ? "alert" : "status")
            .textContent
        ).toContain(
          outcome === "unknown"
            ? "image rebuild could not be confirmed"
            : "Rebuilding sandbox image"
        )

        getWorkspace.mockResolvedValue({
          ...saved,
          refresh_status: outcome === "unknown" ? "success" : outcome,
          refresh_finished_at:
            outcome === "unknown"
              ? saved.refresh_finished_at
              : "2026-01-01T00:01:00Z",
          refresh_error: outcome === "failed" ? "Setup script exited 1" : null,
        })
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5001)
        })
        expect(
          within(general).getByRole(outcome === "success" ? "status" : "alert")
            .textContent
        ).toContain(
          outcome === "unknown"
            ? "image rebuild could not be confirmed"
            : outcome === "failed"
              ? "Image rebuild failed. Setup script exited 1"
              : "Sandbox image rebuilt with the saved repositories."
        )
        expect(
          screen
            .getByRole("button", { name: "Rebuild image" })
            .hasAttribute("disabled")
        ).toBe(false)
        const settledReads = getWorkspace.mock.calls.length
        await act(async () => {
          await vi.advanceTimersByTimeAsync(10001)
        })
        expect(getWorkspace.mock.calls.length).toBe(settledReads)
      } finally {
        vi.useRealTimers()
      }
    }
  )

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
    expect(screen.getAllByText("OPENSWE_WORKSPACE_REPOS")).toHaveLength(2)
    expect(screen.queryByText('OPENSWE_WORKSPACE_REPOS="acme/oss"')).toBeNull()
    for (const trigger of screen.getAllByRole("button", {
      name: "OPENSWE_WORKSPACE_REPOS",
    })) {
      fireEvent.click(trigger)
      const popup = await screen.findByRole("dialog", {
        name: "Expanded value",
      })
      expect(
        within(popup).getByText('OPENSWE_WORKSPACE_REPOS="acme/oss"')
      ).toBeTruthy()
      fireEvent.keyDown(popup, { key: "Escape" })
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    }
    fireEvent.change(setup, { target: { value: "make setup && make build" } })
    fireEvent.click(screen.getByRole("button", { name: "Save scripts" }))
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        setup_script: "make setup && make build",
        update_script: "",
      })
    )

    // Once started, the page re-reads the record and follows the run instead
    // of re-enabling the button on the start request alone.
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockResolvedValue({
      ...RECORD,
      setup_script: "make setup && make build",
      refresh_status: "refreshing",
    })
    const readsBefore = getWorkspace.mock.calls.length
    fireEvent.click(screen.getByRole("button", { name: "Rebuild image" }))
    await waitFor(() => expect(refresh).toHaveBeenCalledWith("oss"))
    expect(await screen.findByText(/Rebuild started/)).toBeTruthy()
    await waitFor(() =>
      expect(getWorkspace.mock.calls.length).toBeGreaterThan(readsBefore)
    )
    const rebuilding = await screen.findByRole("button", {
      name: "Rebuilding…",
    })
    expect((rebuilding as HTMLButtonElement).disabled).toBe(true)
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

    // The chip in General plus the option in the portalled dropdown; Core's repo nowhere.
    expect(screen.getAllByText("acme/oss").length).toBeGreaterThan(1)
    const option = screen.getByRole("button", { name: "acme/oss" })
    expect(option.closest("section")).toBeNull()
    expect(screen.queryByText("acme/api")).toBeNull()
  })

  it("turns an inherited setting into an override and resets it back", async () => {
    mockApis()
    const save = vi
      .spyOn(api, "saveWorkspaceSettings")
      .mockImplementation(async (_slug, overrides) => ({
        effective: { ...SETTINGS, ...overrides },
        overrides,
      }))
    renderPage()

    const fable = (
      await screen.findByRole("heading", { name: "Fable" })
    ).closest("section")
    if (!fable) throw new Error("no Fable section")
    const toggle = within(fable).getByRole("switch")
    await waitFor(() => expect(toggle.hasAttribute("disabled")).toBe(false))
    expect(within(fable).getByText("Inherited")).toBeTruthy()

    fireEvent.click(toggle)
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("oss", { fable_enabled: true })
    )
    expect(await within(fable).findByText("Overridden")).toBeTruthy()

    fireEvent.click(
      within(fable).getByRole("button", {
        name: "Reset Allow Fable models to the instance value",
      })
    )
    await waitFor(() => expect(save).toHaveBeenLastCalledWith("oss", {}))
    expect(await within(fable).findByText("Inherited")).toBeTruthy()
  })
})
