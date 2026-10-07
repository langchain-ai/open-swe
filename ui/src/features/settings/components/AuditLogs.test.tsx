/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { AuditLogs } from "./AuditLogs"
import { api, type AuditLog, type AuditLogsPage } from "@/lib/api"

function event(operation_name: string): AuditLog {
  return {
    id: operation_name,
    request_time: "2026-10-06T12:00:00Z",
    operation_name,
    operation_succeeded: null,
    api_key_id: null,
    user_id: null,
    workspace_id: null,
    enrichments: {
      source: "tool",
      actor_kind: "agent",
      actor_login: "alice",
      request_method: null,
      request_path: null,
      response_status_code: null,
      resource_ids: [],
      workspace: "default",
      thread_id: null,
      delegated_from_sandbox_id: null,
      settings_scope: "instance",
      settings_changes: {
        default_repo: { before: "[REDACTED]", after: null },
      },
    },
  }
}

const clients: QueryClient[] = []
function renderLogs() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  clients.push(client)
  render(
    <QueryClientProvider client={client}>
      <AuditLogs />
    </QueryClientProvider>
  )
}

afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  vi.restoreAllMocks()
})

it("keeps pagination on its applied range and discards late pages after a filter change", async () => {
  let finishPage!: (page: AuditLogsPage) => void
  const list = vi
    .spyOn(api, "listAuditLogs")
    .mockImplementation(async (filters, cursor) => {
      if (filters.operation_name === "save_user_settings") {
        return { items: [event("save_user_settings")], cursor: null }
      }
      if (cursor)
        return new Promise((resolve) => {
          finishPage = resolve
        })
      return { items: [event("old_operation")], cursor: "opaque-cursor" }
    })
  renderLogs()
  await screen.findByText("old_operation")
  const initialFilters = list.mock.calls[0]![0]
  fireEvent.change(screen.getByLabelText("From (local time)"), {
    target: { value: "2026-10-05T00:00:00" },
  })
  fireEvent.change(screen.getByLabelText("To (local time)"), {
    target: { value: "2026-10-06T00:00:00" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Load more" }))
  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith(initialFilters, "opaque-cursor")
  )
  fireEvent.change(screen.getByLabelText("Operation"), {
    target: { value: "save_user_settings" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }))
  await screen.findByText("save_user_settings")
  finishPage({ items: [event("late_operation")], cursor: null })
  await waitFor(() => expect(screen.queryByText("old_operation")).toBeNull())
  expect(screen.queryByText("late_operation")).toBeNull()
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull()
  expect(list).toHaveBeenLastCalledWith(
    {
      start_time: new Date("2026-10-05T00:00:00").toISOString(),
      end_time: new Date("2026-10-06T00:00:00").toISOString(),
      operation_name: "save_user_settings",
    },
    undefined
  )
})

it("rejects an overlong range and preserves unknown outcomes, redacted values, and unset overrides", async () => {
  const list = vi
    .spyOn(api, "listAuditLogs")
    .mockResolvedValue({ items: [event("save_user_settings")], cursor: null })
  renderLogs()
  await screen.findByText("Unknown")
  expect(screen.queryByText("Failed")).toBeNull()
  fireEvent.change(screen.getByLabelText("From (local time)"), {
    target: { value: "2026-01-01T00:00:00" },
  })
  fireEvent.change(screen.getByLabelText("To (local time)"), {
    target: { value: "2026-03-01T00:00:00" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }))
  expect(screen.getByRole("alert").textContent).toContain("at most 31 days")
  expect(list).toHaveBeenCalledTimes(1)
  fireEvent.click(
    screen.getByRole("button", { name: "View save_user_settings event" })
  )
  await screen.findByRole("dialog")
  expect(screen.getByText("[REDACTED]")).toBeTruthy()
  expect(screen.getByText("Unset")).toBeTruthy()
})
