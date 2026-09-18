/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { waitFor } from "@testing-library/dom"

import { WorkspacesSection } from "./WorkspacesSection"
import { api } from "@/lib/api"

const navigate = vi.fn()
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }))
vi.mock("@/features/agents/lib/workspaceRefreshFix", () => ({
  stageWorkspaceRefreshFix: () => "fix-id",
}))

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
  it("lets admins fix or rerun failed refreshes", async () => {
    vi.spyOn(api, "refreshWorkspace").mockResolvedValue({
      started: true,
      run_id: "run-1",
    })
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
          has_snapshot: true,
          has_update_script: true,
          refresh_status: "failed",
          refresh_finished_at: new Date(Date.now() - 60_000).toISOString(),
          refresh_error: "setup script exited 1",
        },
      ],
    })

    const view = renderSection(true)

    expect(await screen.findByText("Preview")).toBeTruthy()
    expect(screen.getByText("Default workspace · Snapshot ready")).toBeTruthy()
    expect(screen.getByText(/Updated 1 hour ago/)).toBeTruthy()
    expect(screen.getByText(/Refresh failed/)).toBeTruthy()
    expect(screen.getByText("setup script exited 1")).toBeTruthy()
    expect(screen.getByText("Refresh log")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Fix" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Rerun" })).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Fix" }))
    expect(navigate).toHaveBeenCalledWith({ href: "/agents?fix=fix-id" })
    fireEvent.click(screen.getByRole("button", { name: "Rerun" }))
    await waitFor(() =>
      expect(api.refreshWorkspace).toHaveBeenCalledWith("preview", "update")
    )
    expect(view.container.querySelector("input, textarea")).toBeNull()
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
          refresh_status: "failed",
          refresh_kind: "full",
          refresh_finished_at: new Date(Date.now() - 3_600_000).toISOString(),
          refresh_log_excerpt: "+ TOKEN=hunter2",
        },
      ],
    })

    renderSection(false)

    expect(await screen.findByText(/Refresh failed 1 hour ago/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Fix" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Rerun" })).toBeNull()
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
    expect(screen.getByText("Default workspace · No snapshot")).toBeTruthy()
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
