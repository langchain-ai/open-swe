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
import { api } from "@/lib/api"

const clients: Array<QueryClient> = []

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.restoreAllMocks()
})

function renderSection(isAdmin: boolean) {
  // The route supplies the link to each workspace's page; a button stands in.
  const renderConfigure = (workspace: { slug: string; name: string }) => (
    <button type="button">{`Configure ${workspace.name}`}</button>
  )
  // The pickers browse Slack and the GitHub installation; tests that care
  // mock these before rendering, the rest get empty directories.
  if (!vi.isMockFunction(api.listSlackChannels)) {
    vi.spyOn(api, "listSlackChannels").mockResolvedValue({
      channels: [],
      partial: false,
    })
  }
  if (!vi.isMockFunction(api.me)) {
    vi.spyOn(api, "me").mockRejectedValue(new Error("not signed in"))
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <WorkspacesSection isAdmin={isAdmin} renderConfigure={renderConfigure} />
    </QueryClientProvider>
  )
}

describe("WorkspacesSection", () => {
  it("shows refresh outcomes and a configure control for admins", async () => {
    const finishedAt = new Date(Date.now() - 3_600_000).toISOString()
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "default",
      workspaces: [
        {
          slug: "default",
          name: "Default",
          repos: [],
          slack_channel_ids: [],
          is_default: true,
          default_repo: null,
          has_snapshot: true,
          refresh_status: "success",
          refresh_kind: "update",
          refresh_finished_at: finishedAt,
          refresh_log_excerpt: "cloning acme/repo\ndone",
        },
        {
          slug: "preview",
          name: "Preview",
          repos: [],
          slack_channel_ids: [],
          is_default: false,
          default_repo: null,
          has_snapshot: false,
          refresh_status: "failed",
          refresh_finished_at: new Date(Date.now() - 60_000).toISOString(),
          refresh_error: "setup script exited 1",
        },
      ],
    })

    renderSection(true)

    expect(await screen.findByText("Preview")).toBeTruthy()
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull()
    expect(screen.getByText("Snapshot ready")).toBeTruthy()
    const updated = screen.getByText(/Updated 1 hour ago/)
    expect(updated.getAttribute("title")).toBe(
      new Date(finishedAt).toLocaleString(undefined, { timeZoneName: "short" })
    )
    expect(screen.getByText(/Refresh failed/)).toBeTruthy()
    expect(screen.getByText("setup script exited 1")).toBeTruthy()
    expect(screen.getByText("Refresh log")).toBeTruthy()
    expect(
      screen.getByRole("button", { name: "Configure Default" })
    ).toBeTruthy()
    expect(
      screen.getByRole("button", { name: "Configure Preview" })
    ).toBeTruthy()
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
          default_repo: null,
          has_snapshot: true,
        },
        {
          slug: "preview",
          name: "Preview",
          repos: [],
          slack_channel_ids: [],
          is_default: false,
          default_repo: null,
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
    const chip = within(defaultRow).getByRole("link", { name: "acme/oss" })
    expect(chip.getAttribute("href")).toBe("https://github.com/acme/oss")
    expect(chip.getAttribute("target")).toBe("_blank")
    expect(chip.getAttribute("rel")).toBe("noopener noreferrer")
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
          default_repo: null,
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
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull()
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
          default_repo: null,
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

  it("creates a workspace from the create form with a repository and a picked channel", async () => {
    vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
      default_slug: "core",
      workspaces: [
        {
          slug: "core",
          name: "Core",
          repos: ["acme/api"],
          slack_channel_ids: ["C0000000001"],
          is_default: true,
          default_repo: null,
          has_snapshot: true,
        },
      ],
    })
    vi.spyOn(api, "listSlackChannels").mockResolvedValue({
      channels: [
        {
          id: "C0000000001",
          name: "commits",
          is_private: false,
          is_member: true,
          is_ext_shared: false,
          num_members: 12,
        },
        {
          id: "C0000000002",
          name: "oss-help",
          is_private: false,
          is_member: true,
          is_ext_shared: false,
          num_members: 40,
        },
      ],
      partial: true,
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
    const repositoryHelp = screen.getByRole("button", {
      name: "About workspace repositories",
    })
    fireEvent.mouseEnter(repositoryHelp)
    fireEvent.mouseMove(repositoryHelp)
    expect(
      await screen.findByText(
        /routes its GitHub issues, pull requests, Linear tickets/
      )
    ).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Choose repositories" }))
    fireEvent.change(await screen.findByLabelText("Add a repository by name"), {
      target: { value: "acme/web" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))
    fireEvent.click(screen.getByRole("button", { name: "Save 1 repository" }))

    fireEvent.click(screen.getByRole("button", { name: "Choose channels" }))
    // Core's channel is offered but not selectable; the free one is. The
    // directory came back partial, and the picker says so.
    const taken = await screen.findByRole("checkbox", { name: "#commits" })
    expect(
      screen.getByText(/only channels the bot is in are listed/)
    ).toBeTruthy()
    expect(taken.hasAttribute("disabled")).toBe(true)
    fireEvent.click(screen.getByRole("checkbox", { name: "#oss-help" }))
    fireEvent.click(screen.getByRole("button", { name: "Save 1 channel" }))
    expect(screen.getByText("#oss-help")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }))

    await waitFor(() => expect(createSpy).toHaveBeenCalled())
    expect(createSpy).toHaveBeenCalledWith({
      name: "Preview",
      repos: ["acme/web"],
      slack_channel_ids: ["C0000000002"],
    })
  })
})
