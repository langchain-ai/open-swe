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
import { api, type WorkspaceApiKey } from "@/lib/api"
import { WorkspaceApiKeysSection } from "./WorkspaceApiKeysSection"

const key: WorkspaceApiKey = {
  id: "key-1",
  workspace: "default",
  name: "Release automation",
  key_suffix: "123456",
  created_by: "alice",
  created_at: null,
  expires_at: "2027-01-01T00:00:00Z",
  last_used_at: null,
  revoked_at: null,
  status: "active",
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceApiKeysSection slug="default" />
    </QueryClientProvider>
  )
  return client
}

it("creates a workspace key and discards the one-time secret without caching it", async () => {
  vi.spyOn(api, "listWorkspaceApiKeys").mockResolvedValue([])
  const create = vi
    .spyOn(api, "createWorkspaceApiKey")
    .mockResolvedValue({ ...key, secret: "osk_test_secret" })
  const client = mount()
  await screen.findByText("No API keys yet.")
  fireEvent.change(screen.getByLabelText("Key name"), {
    target: { value: "Release automation" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Create API key" }))
  const input = (await screen.findByLabelText(
    "New API key"
  )) as HTMLInputElement
  expect(input.value).toBe("osk_test_secret")
  expect(create).toHaveBeenCalledWith(
    expect.objectContaining({
      workspace: "default",
      name: "Release automation",
    })
  )
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data)
    )
  ).not.toContain("osk_test_secret")
  fireEvent.click(screen.getByRole("button", { name: "I’ve saved my key" }))
  expect(screen.queryByLabelText("New API key")).toBeNull()
})

it("requires confirmation to revoke and keeps failures recoverable", async () => {
  vi.spyOn(api, "listWorkspaceApiKeys").mockResolvedValue([key])
  const revoke = vi
    .spyOn(api, "revokeWorkspaceApiKey")
    .mockRejectedValueOnce(new Error("Request failed"))
    .mockResolvedValueOnce(undefined)
  mount()
  fireEvent.click(
    await screen.findByRole("button", { name: "Revoke Release automation" })
  )
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(revoke).not.toHaveBeenCalled()
  fireEvent.click(
    screen.getByRole("button", { name: "Revoke Release automation" })
  )
  fireEvent.click(screen.getByRole("button", { name: "Revoke key" }))
  await screen.findAllByText("Request failed")
  fireEvent.click(screen.getByRole("button", { name: "Revoke key" }))
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
  expect(revoke).toHaveBeenCalledTimes(2)
})
