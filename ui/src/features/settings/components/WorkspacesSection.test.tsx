/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
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
})
