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
  type WorkspaceSettingsView,
} from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import { INVALIDATION_TOPICS } from "@/lib/invalidations/topics"
import { makeQueryClient } from "@/lib/query"

import { WorkspaceSettingsPanel } from "./WorkspaceSettings"

vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))
vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onChange,
    options,
  }: {
    value: string
    onChange: (value: string) => void
    options: { ariaLabel: string }
  }) => (
    <textarea
      aria-label={options.ariaLabel}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

const RECORD: WorkspaceRecord = {
  slug: "oss",
  name: "OSS",
  prompt: "Run make test.",
  repos: ["acme/oss"],
  slack_channel_ids: ["C1"],
  kitchen_channel_ids: [],
  setup_script: "make setup",
  update_script: "",
  base_snapshot_id: null,
  snapshot_id: "snapshot-1",
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
  fable_enabled: false,
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
        kitchen_channel_ids: [],
        is_default: true,
        default_repo: null,
        has_snapshot: true,
      },
      {
        slug: "core",
        name: "Core",
        repos: ["acme/api"],
        slack_channel_ids: [],
        kitchen_channel_ids: [],
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
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(SETTINGS)
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
  vi.spyOn(api, "listWorkspaceApiKeys").mockResolvedValue([])
  vi.spyOn(api, "me").mockRejectedValue(new Error("not signed in"))
}

function renderPage(canEdit = true, onDeleted = vi.fn(), slug = "oss") {
  const client = makeQueryClient()
  client.setDefaultOptions({ queries: { retry: false } })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceSettingsPanel
        slug={slug}
        canEdit={canEdit}
        onDeleted={onDeleted}
      />
    </QueryClientProvider>
  )
}

/** What `InvalidationTab` does when the stream invalidates `workspaces`. */
async function invalidateWorkspaces() {
  await act(async () => {
    await clients.at(-1)?.invalidateQueries({
      predicate: (query) =>
        query.meta?.invalidatedBy?.includes(INVALIDATION_TOPICS.workspaces) ??
        false,
    })
    await vi.advanceTimersByTimeAsync(1)
  })
}

describe("WorkspaceSettingsPanel", () => {
  it("shows model labels before the dropdown opens and after it closes", async () => {
    mockApis()
    vi.mocked(api.options).mockResolvedValue({
      models: [
        {
          id: "openai:gpt-6.1-sol",
          label: "GPT-6.1 Sol",
          supports_images: true,
          efforts: ["medium"],
          default_effort: "medium",
        },
      ],
      default_agent_model: "openai:gpt-6.1-sol",
      default_agent_reasoning_effort: "medium",
      default_agent_subagent_model: "openai:gpt-6.1-sol",
      default_agent_subagent_reasoning_effort: "medium",
    })
    vi.mocked(api.getWorkspaceSettings).mockResolvedValue({
      effective: {
        ...SETTINGS,
        default_agent_model: "openai:gpt-6.1-sol",
        default_agent_reasoning_effort: "medium",
      },
      overrides: {},
    })
    renderPage()

    const trigger = (await screen.findByText("GPT-6.1 Sol")).closest("button")!
    const models = screen
      .getByRole("heading", { name: "Model defaults" })
      .closest("section")!
    expect(within(models).getByText("Inherit instance setting")).toBeTruthy()
    expect(screen.queryByRole("listbox")).toBeNull()
    fireEvent.click(trigger)
    expect(
      await screen.findByRole("option", { name: "GPT-6.1 Sol" })
    ).toBeTruthy()
    fireEvent.keyDown(trigger, { key: "Escape" })
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull())
    expect(within(trigger).getByText("GPT-6.1 Sol")).toBeTruthy()
  })

  it("offers no way to delete the default workspace", async () => {
    mockApis({ ...RECORD, slug: "default", name: "Default" })
    renderPage(true, vi.fn(), "default")

    expect(await screen.findByRole("heading", { name: "General" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Delete Default" })).toBeNull()
    const repository = screen.getByRole("link", { name: "acme/oss" })
    expect(repository.getAttribute("href")).toBe("https://github.com/acme/oss")
    expect(repository.getAttribute("target")).toBe("_blank")
    const channel = await screen.findByRole("link", { name: "#oss-help" })
    expect(channel.getAttribute("href")).toBe(
      "https://slack.com/app_redirect?channel=C1"
    )
    expect(channel.getAttribute("target")).toBe("_blank")
  })

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
    await waitFor(() =>
      expect(reportError).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't delete workspace",
          error: expect.objectContaining({
            message: "Could not delete the workspace",
          }),
        })
      )
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

    const general = name.closest("section")!
    expect(within(general).queryByRole("button", { name: "Cancel" })).toBeNull()
    fireEvent.change(name, { target: { value: "Discard this" } })
    fireEvent.click(within(general).getByRole("button", { name: "Cancel" }))
    expect((name as HTMLInputElement).value).toBe("OSS")
    expect(within(general).queryByRole("button", { name: "Cancel" })).toBeNull()

    fireEvent.change(name, { target: { value: " OSS support " } })
    expect(within(general).getByRole("button", { name: "Cancel" })).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        name: "OSS support",
        repos: ["acme/oss"],
        slack_channel_ids: ["C1"],
        kitchen_channel_ids: [],
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
    "follows a repository rebuild from a %s save response through invalidations to %s",
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
        expect(
          within(general).queryByRole("button", { name: "Cancel" })
        ).toBeNull()
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
        await invalidateWorkspaces()
        expect(getWorkspace.mock.calls.length).toBeGreaterThan(reads)
        expect(screen.getByRole("status").textContent).toContain(
          "rebuild queued"
        )

        getWorkspace.mockResolvedValue({
          ...saved,
          refresh_status: outcome === "unknown" ? "success" : "refreshing",
        })
        await invalidateWorkspaces()
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
        await invalidateWorkspaces()
        expect(
          within(general).getByRole(outcome === "success" ? "status" : "alert")
            .textContent
        ).toContain(
          outcome === "unknown"
            ? "image rebuild could not be confirmed"
            : outcome === "failed"
              ? "Image rebuild failed. Setup script exited 1"
              : "Sandbox image built with the saved repositories."
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

  it("saves sandbox sizes in bytes and clears inherited sizes", async () => {
    mockApis()
    const update = vi.spyOn(api, "updateWorkspace").mockResolvedValue(RECORD)
    renderPage()
    fireEvent.change(await screen.findByLabelText("vCPUs"), {
      target: { value: "8" },
    })
    fireEvent.change(screen.getByLabelText("Memory (GiB)"), {
      target: { value: "32" },
    })
    fireEvent.change(screen.getByLabelText("Disk (GiB)"), {
      target: { value: "256" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Save sandbox configuration" })
    )
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        vcpus: 8,
        mem_bytes: 32 * 1024 ** 3,
        fs_capacity_bytes: 256 * 1024 ** 3,
      })
    )
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "Save sandbox configuration" })
          .hasAttribute("disabled")
      ).toBe(false)
    )
    for (const label of ["vCPUs", "Memory (GiB)", "Disk (GiB)"])
      fireEvent.change(screen.getByLabelText(label), { target: { value: "" } })
    fireEvent.click(
      screen.getByRole("button", { name: "Save sandbox configuration" })
    )
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        vcpus: null,
        mem_bytes: null,
        fs_capacity_bytes: null,
      })
    )
  })

  it("edits proxy configuration without losing other sandbox create parameters", async () => {
    const record = {
      ...RECORD,
      create_params: { _internal_runtime: "v2", proxy_config: { rules: [] } },
    }
    mockApis(record)
    const proxy = {
      rules: [
        {
          name: "service",
          match_hosts: ["api.example.com"],
          env_vars: { SERVICE_MODE: "example" },
        },
      ],
    }
    const update = vi.spyOn(api, "updateWorkspace").mockResolvedValue({
      ...record,
      create_params: { ...record.create_params, proxy_config: proxy },
    })
    renderPage()
    fireEvent.click(await screen.findByRole("tab", { name: "JSON" }))
    const editor = await screen.findByLabelText("Proxy configuration (JSON)")
    fireEvent.change(editor, { target: { value: "[]" } })
    fireEvent.click(
      screen.getByRole("button", { name: "Save proxy configuration" })
    )
    expect(
      await screen.findByText("Proxy configuration must be a JSON object.")
    ).toBeTruthy()
    expect(update).not.toHaveBeenCalled()
    fireEvent.change(editor, { target: { value: JSON.stringify(proxy) } })
    fireEvent.click(screen.getByRole("tab", { name: "Rules" }))
    fireEvent.change(screen.getByLabelText("Rule name"), {
      target: { value: "updated-service" },
    })
    fireEvent.click(screen.getByRole("tab", { name: "JSON" }))
    expect(
      JSON.parse(
        (
          screen.getByLabelText(
            "Proxy configuration (JSON)"
          ) as HTMLTextAreaElement
        ).value
      ).rules[0].name
    ).toBe("updated-service")
    fireEvent.click(screen.getByRole("tab", { name: "Rules" }))
    fireEvent.click(
      screen.getByRole("button", { name: "Save proxy configuration" })
    )
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        create_params: {
          _internal_runtime: "v2",
          proxy_config: {
            ...proxy,
            rules: [{ ...proxy.rules[0], name: "updated-service" }],
          },
        },
      })
    )
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Save proxy configuration",
          }) as HTMLButtonElement
        ).disabled
      ).toBe(true)
    )
  })

  it("saves the sandbox scripts and starts a rebuild", async () => {
    mockApis()
    const update = vi.spyOn(api, "updateWorkspace").mockResolvedValue({
      ...RECORD,
      setup_script: "make setup && make build",
      update_script: "git pull --ff-only",
    })
    const refresh = vi
      .spyOn(api, "refreshWorkspace")
      .mockResolvedValue({ started: true, run_id: "run-1" })
    renderPage()

    await screen.findByRole("button", { name: "Edit setup script" })
    const sandbox = screen
      .getByRole("button", { name: "Save scripts" })
      .closest("section")!
    expect(within(sandbox).queryByRole("button", { name: "Cancel" })).toBeNull()
    expect(screen.queryByLabelText("Setup script")).toBeNull()
    for (const name of ["Edit setup script", "Edit update script"]) {
      fireEvent.click(screen.getByRole("button", { name }))
      fireEvent.click(
        await screen.findByRole("button", { name: "OPENSWE_WORKSPACE_REPOS" })
      )
      const popup = await screen.findByRole("dialog", {
        name: "Expanded value",
      })
      expect(
        within(popup).getByText('OPENSWE_WORKSPACE_REPOS="acme/oss"')
      ).toBeTruthy()
      fireEvent.keyDown(popup, { key: "Escape" })
      await waitFor(() =>
        expect(
          screen.queryByRole("dialog", { name: "Expanded value" })
        ).toBeNull()
      )
      fireEvent.click(screen.getByRole("button", { name: "Done" }))
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    }
    fireEvent.click(screen.getByRole("button", { name: "Edit setup script" }))
    const setup = await screen.findByRole("textbox", { name: "Setup script" })
    expect((setup as HTMLTextAreaElement).value).toBe("make setup")
    fireEvent.change(setup, { target: { value: "Discard this" } })
    fireEvent.click(screen.getByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    fireEvent.click(within(sandbox).getByRole("button", { name: "Cancel" }))
    expect(within(sandbox).queryByRole("button", { name: "Cancel" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Edit setup script" }))
    const restoredSetup = await screen.findByRole("textbox", {
      name: "Setup script",
    })
    expect((restoredSetup as HTMLTextAreaElement).value).toBe("make setup")
    fireEvent.change(restoredSetup, {
      target: { value: "make setup && make build" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(update).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Edit update script" }))
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Update script" }),
      {
        target: { value: "git pull --ff-only" },
      }
    )
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    fireEvent.click(screen.getByRole("button", { name: "Edit setup script" }))
    expect(
      (
        (await screen.findByRole("textbox", {
          name: "Setup script",
        })) as HTMLTextAreaElement
      ).value
    ).toBe("make setup && make build")
    fireEvent.click(screen.getByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "Unsaved workspace name" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save scripts" }))
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("oss", {
        setup_script: "make setup && make build",
        update_script: "git pull --ff-only",
      })
    )

    await waitFor(() =>
      expect(
        within(
          screen
            .getByRole("button", { name: "Save scripts" })
            .closest("section")!
        ).queryByRole("button", { name: "Cancel" })
      ).toBeNull()
    )

    expect(
      (screen.getByLabelText("Workspace name") as HTMLInputElement).value
    ).toBe("Unsaved workspace name")
    expect(
      (
        screen.getByRole("button", {
          name: "Save",
        }) as HTMLButtonElement
      ).disabled
    ).toBe(false)

    // Once started, the page re-reads the record and follows the run instead
    // of re-enabling the button on the start request alone.
    const getWorkspace = vi.spyOn(api, "getWorkspace").mockResolvedValue({
      ...RECORD,
      setup_script: "make setup && make build",
      update_script: "git pull --ff-only",
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

  it("offers every accessible repository as its default", async () => {
    mockApis()
    // The repository list loads for a signed-in user.
    vi.spyOn(api, "me").mockResolvedValue({
      login: "alice",
      email: null,
      avatar_url: null,
      is_admin: true,
    })
    vi.spyOn(api, "repos").mockResolvedValue({
      installations: [],
      repositories: [
        { full_name: "acme/oss", private: false, archived: false },
        { full_name: "acme/api", private: true, archived: false },
        { full_name: "acme/legacy", private: false, archived: true },
      ],
    })
    renderPage()

    // The selector stays disabled until the workspace's settings have loaded.
    const trigger = await screen.findByRole("button", {
      name: /Pick a repository/,
    })
    await waitFor(() => expect(trigger.hasAttribute("disabled")).toBe(false))
    fireEvent.click(trigger)

    // Core prefers acme/api, and OSS can still default to it.
    const option = await screen.findByRole("button", {
      name: /acme\/api\s*Private/,
    })
    expect(option.closest("section")).toBeNull()
    expect(screen.getAllByText("acme/oss").length).toBeGreaterThan(1)
    expect(screen.queryByText("acme/legacy")).toBeNull()
    fireEvent.click(screen.getByRole("checkbox", { name: "Show archived" }))
    expect(
      screen.getByRole("button", { name: /acme\/legacy\s*Public\s*archive/ })
    ).toBeTruthy()
  })

  it("turns an inherited setting into an override and resets it back", async () => {
    mockApis()
    let stored: WorkspaceSettingsView = { effective: SETTINGS, overrides: {} }
    vi.spyOn(api, "getWorkspaceSettings").mockImplementation(async () => stored)
    const save = vi
      .spyOn(api, "saveWorkspaceSettings")
      .mockImplementation(async (_slug, overrides) => {
        stored = { effective: { ...SETTINGS, ...overrides }, overrides }
        return stored
      })
    renderPage()

    const row = (await screen.findByText("Review Draft PRs")).closest(
      "label"
    )!.parentElement!
    const toggle = within(row).getByRole("switch")
    await waitFor(() => expect(toggle.hasAttribute("disabled")).toBe(false))
    expect(toggle.getAttribute("aria-checked")).toBe("false")

    fireEvent.click(toggle)
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("oss", { review_draft_prs: true })
    )
    expect(toggle.getAttribute("aria-checked")).toBe("true")

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Reset Review Draft PRs to the instance value",
      })
    )
    await waitFor(() => expect(save).toHaveBeenLastCalledWith("oss", {}))
    await waitFor(() =>
      expect(toggle.getAttribute("aria-checked")).toBe("false")
    )
    expect(
      screen.queryByRole("button", {
        name: "Reset Review Draft PRs to the instance value",
      })
    ).toBeNull()
  })
})
