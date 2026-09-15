/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { WorkspacesSection } from "./WorkspacesSection"
import { api, ApiError } from "@/lib/api"

const clients: Array<QueryClient> = []

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.restoreAllMocks()
})

function renderSection(isAdmin: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <WorkspacesSection isAdmin={isAdmin} />
    </QueryClientProvider>
  )
}

describe("WorkspacesSection", () => {
  it("shows refresh outcomes and an edit control for admins", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "default",
      workspaces: [
        {
          slug: "default",
          name: "Default",
          repos: [],
          slack_channel_ids: [],
          is_default: true,
          has_snapshot: true,
          refresh_status: "success",
          refresh_kind: "update",
          refresh_finished_at: new Date(Date.now() - 3_600_000).toISOString(),
          refresh_log_excerpt: "cloning acme/repo\ndone",
        },
        {
          slug: "preview",
          name: "Preview",
          repos: [],
          slack_channel_ids: [],
          is_default: false,
          has_snapshot: false,
          refresh_status: "failed",
          refresh_finished_at: new Date(Date.now() - 60_000).toISOString(),
          refresh_error: "setup script exited 1",
        },
      ],
    })

    renderSection(true)

    expect(await screen.findByText("Preview")).toBeTruthy()
    expect(screen.getByText("Snapshot ready")).toBeTruthy()
    expect(screen.getByText(/Updated 1 hour ago/)).toBeTruthy()
    expect(screen.getByText(/Refresh failed/)).toBeTruthy()
    expect(screen.getByText("setup script exited 1")).toBeTruthy()
    expect(screen.getByText("Refresh log")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Edit Default" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Edit Preview" })).toBeTruthy()
  })

  it("renders a workspace's repos under its row, and a Default badge on the default workspace", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "default",
      workspaces: [
        {
          slug: "default",
          name: "Primary",
          repos: ["acme/oss"],
          slack_channel_ids: [],
          is_default: true,
          has_snapshot: true,
        },
        {
          slug: "preview",
          name: "Preview",
          repos: [],
          slack_channel_ids: [],
          is_default: false,
          has_snapshot: false,
        },
      ],
    })

    renderSection(true)

    const defaultRow = (await screen.findByText("Primary")).closest(
      "[data-workspace-row]"
    ) as HTMLElement
    const previewRow = screen
      .getByText("Preview")
      .closest("[data-workspace-row]") as HTMLElement
    expect(defaultRow).toBeTruthy()
    expect(within(defaultRow).getByText("acme/oss")).toBeTruthy()
    expect(within(defaultRow).getByText("Default")).toBeTruthy()
    expect(within(previewRow).queryByText("Default")).toBeNull()
  })

  it("never renders a refresh log for non-admins, even if one arrives", async () => {
    // The API already omits it; a `bash -x` trace can carry expanded
    // credentials, so the row refuses to show one regardless.
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "default",
      workspaces: [
        {
          slug: "default",
          name: "Default",
          repos: [],
          slack_channel_ids: [],
          is_default: true,
          has_snapshot: true,
          refresh_status: "success",
          refresh_kind: "full",
          refresh_finished_at: new Date(Date.now() - 3_600_000).toISOString(),
          refresh_log_excerpt: "+ TOKEN=hunter2",
        },
      ],
    })

    renderSection(false)

    expect(await screen.findByText(/Rebuilt 1 hour ago/)).toBeTruthy()
    expect(screen.queryByText("Refresh log")).toBeNull()
    expect(screen.queryByText(/hunter2/)).toBeNull()
  })

  it("says so when a workspace has never been refreshed", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "default",
      workspaces: [
        {
          slug: "default",
          name: "Default",
          repos: [],
          slack_channel_ids: [],
          is_default: true,
          has_snapshot: false,
        },
      ],
    })

    renderSection(true)

    expect(await screen.findByText("Never refreshed")).toBeTruthy()
    expect(screen.getByText("No snapshot")).toBeTruthy()
  })

  it("directs non-admins to a workspace admin", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "default",
      workspaces: [],
    })

    renderSection(false)

    expect(
      await screen.findByText("No workspaces are configured.")
    ).toBeTruthy()
    expect(screen.getByText(/ask a workspace admin/)).toBeTruthy()
  })

  it("loads the full record on Edit and prefills the existing prompt", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "core",
      workspaces: [
        {
          slug: "core",
          name: "Core",
          repos: ["acme/api"],
          slack_channel_ids: ["C0000000001"],
          is_default: true,
          has_snapshot: true,
        },
      ],
    })
    const getWorkspaceSpy = vi.spyOn(api, "getWorkspace").mockResolvedValue({
      slug: "core",
      name: "Core",
      prompt: "Existing instructions for core",
      repos: ["acme/api"],
      slack_channel_ids: ["C0000000001"],
    })

    renderSection(true)

    fireEvent.click(await screen.findByRole("button", { name: "Edit Core" }))
    const promptField = (await screen.findByLabelText(
      "Instructions"
    )) as HTMLTextAreaElement
    expect(promptField.value).toBe("Existing instructions for core")
    expect(getWorkspaceSpy).toHaveBeenCalledWith("core")
  })

  it("saves edited repos with the trimmed list and the loaded prompt", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "core",
      workspaces: [
        {
          slug: "core",
          name: "Core",
          repos: ["acme/api"],
          slack_channel_ids: ["C0000000001"],
          is_default: true,
          has_snapshot: true,
        },
      ],
    })
    vi.spyOn(api, "getWorkspace").mockResolvedValue({
      slug: "core",
      name: "Core",
      prompt: "Existing instructions for core",
      repos: ["acme/api"],
      slack_channel_ids: ["C0000000001"],
    })
    const updateSpy = vi.spyOn(api, "updateWorkspace").mockResolvedValue({
      slug: "core",
      name: "Core",
      prompt: "Existing instructions for core",
      repos: ["acme/api", "acme/web"],
      slack_channel_ids: ["C0000000001"],
    })

    renderSection(true)

    fireEvent.click(await screen.findByRole("button", { name: "Edit Core" }))
    await screen.findByLabelText("Instructions")
    fireEvent.change(screen.getByLabelText("Repositories"), {
      target: { value: "acme/api\nacme/web\n" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(updateSpy).toHaveBeenCalled())
    expect(updateSpy).toHaveBeenCalledWith("core", {
      name: "Core",
      repos: ["acme/api", "acme/web"],
      slack_channel_ids: ["C0000000001"],
      prompt: "Existing instructions for core",
    })
  })

  it("renders a 409 conflict's detail message in the alert region", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "core",
      workspaces: [
        {
          slug: "core",
          name: "Core",
          repos: ["acme/api"],
          slack_channel_ids: [],
          is_default: true,
          has_snapshot: true,
        },
      ],
    })
    vi.spyOn(api, "getWorkspace").mockResolvedValue({
      slug: "core",
      name: "Core",
      prompt: "",
      repos: ["acme/api"],
      slack_channel_ids: [],
    })
    vi.spyOn(api, "updateWorkspace").mockRejectedValue(
      new ApiError(409, "repository acme/api already belongs to workspace core")
    )

    renderSection(true)

    fireEvent.click(await screen.findByRole("button", { name: "Edit Core" }))
    await screen.findByLabelText("Instructions")
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toBe(
      "repository acme/api already belongs to workspace core"
    )
  })

  it("creates a workspace from the create form with name, repos, and slack channel ids", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "core",
      workspaces: [],
    })
    const createSpy = vi.spyOn(api, "createWorkspace").mockResolvedValue({
      slug: "preview",
      name: "Preview",
      prompt: "",
      repos: ["acme/api"],
      slack_channel_ids: ["C0000000002"],
    })

    renderSection(true)

    fireEvent.click(
      await screen.findByRole("button", { name: "Add workspace" })
    )
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "Preview" },
    })
    fireEvent.change(screen.getByLabelText("Repositories"), {
      target: { value: "acme/api\n" },
    })
    fireEvent.change(screen.getByLabelText("Slack channel IDs"), {
      target: { value: "C0000000002\n" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }))

    await waitFor(() => expect(createSpy).toHaveBeenCalled())
    expect(createSpy).toHaveBeenCalledWith({
      name: "Preview",
      repos: ["acme/api"],
      slack_channel_ids: ["C0000000002"],
    })
  })
})
