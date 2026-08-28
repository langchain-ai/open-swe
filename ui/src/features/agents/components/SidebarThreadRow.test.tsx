/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { ReactNode } from "react"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: { children?: ReactNode }) => (
    <a {...props}>{children}</a>
  ),
  useNavigate: () => vi.fn(),
}))

import { SidebarThreadRow } from "./SidebarThreadRow"
import { localSidebarThread } from "@/features/agents/lib/sidebarThreads"
import type { DesktopLocalThreadSummary } from "@/desktop"

const local: DesktopLocalThreadSummary = {
  id: "local-1",
  cwd: "/Users/example/demo",
  title: "Local thread",
  viewed: true,
  createdAt: 1,
  updatedAt: 2,
  modelId: null,
  effort: null,
}

function renderRow(archived: boolean, onToggleArchived = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SidebarThreadRow
        item={localSidebarThread({ ...local, archived }, undefined, undefined)}
        isActive={false}
        pinned={false}
        archived={archived}
        onDeleteLocal={vi.fn()}
        onTogglePin={vi.fn()}
        onToggleArchived={onToggleArchived}
      />
    </QueryClientProvider>
  )
  return onToggleArchived
}

afterEach(cleanup)

describe("SidebarThreadRow archiving", () => {
  it("archives a local thread — the action is not cloud-only", () => {
    const onToggleArchived = renderRow(false)
    fireEvent.click(screen.getByLabelText("Archive thread"))
    expect(onToggleArchived).toHaveBeenCalledTimes(1)
  })

  it("shows an archived row as archived and offers unarchive", () => {
    renderRow(true)
    // Without a distinct rendering, archiving looks like a no-op whenever
    // "Show archived" keeps the row on screen.
    expect(screen.queryByLabelText("Archive thread")).toBeNull()
    expect(screen.getByLabelText("Unarchive thread")).toBeTruthy()
  })
})
