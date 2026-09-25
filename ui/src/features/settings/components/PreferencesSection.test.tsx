// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { ConfirmProvider } from "@/components/ConfirmDialog"
import { api, type UserPreferences } from "@/lib/api"
import { PreferencesSection } from "./PreferencesSection"

vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("keeps preference labels when closed and saves workspace values, not labels", async () => {
  let stored: UserPreferences = {
    default_visibility: "private",
    default_workspace: null,
    follow_up_behavior: "queue",
    local_tracing_project: null,
    default_local_tracing_project: "shared",
  }
  vi.spyOn(api, "getMyPreferences").mockImplementation(async () => stored)
  vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
    default_slug: "engineering",
    workspaces: [
      {
        slug: "engineering",
        name: "Engineering workspace",
        repos: [],
        default_repo: null,
        slack_channel_ids: [],
        is_default: true,
        has_snapshot: true,
      },
    ],
  })
  const save = vi
    .spyOn(api, "saveMyPreferences")
    .mockImplementation(async (value) => {
      stored = value
      return value
    })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <PreferencesSection />
      </ConfirmProvider>
    </QueryClientProvider>
  )
  const workspace = screen.getAllByRole("combobox")[2]!

  await waitFor(() => expect(workspace.hasAttribute("disabled")).toBe(false))
  expect(
    screen.getAllByRole("combobox").map((element) => element.textContent)
  ).toEqual(
    ["System", "Private · only me", "Workspace default", "Queue"].map((label) =>
      expect.stringContaining(label)
    )
  )
  expect(save).not.toHaveBeenCalled()

  fireEvent.click(workspace)
  expect(
    await screen.findByRole("option", { name: "Workspace default" })
  ).toBeTruthy()
  fireEvent.keyDown(workspace, { key: "Escape" })
  await waitFor(() => expect(screen.queryByRole("option")).toBeNull())
  expect(workspace.textContent).toContain("Workspace default")
  expect(save).not.toHaveBeenCalled()

  fireEvent.click(workspace)
  const engineering = await screen.findByRole("option", {
    name: "Engineering workspace",
  })
  fireEvent.pointerDown(engineering)
  fireEvent.click(engineering)
  await waitFor(() =>
    expect(save.mock.lastCall?.[0]).toEqual(
      expect.objectContaining({ default_workspace: "engineering" })
    )
  )
  await waitFor(() =>
    expect(workspace.textContent).toContain("Engineering workspace")
  )
  await waitFor(() => expect(workspace.hasAttribute("disabled")).toBe(false))

  fireEvent.click(workspace)
  const workspaceDefault = await screen.findByRole("option", {
    name: "Workspace default",
  })
  fireEvent.pointerDown(workspaceDefault)
  fireEvent.click(workspaceDefault)
  await waitFor(() =>
    expect(save.mock.lastCall?.[0]).toEqual(
      expect.objectContaining({ default_workspace: null })
    )
  )
  await waitFor(() =>
    expect(workspace.textContent).toContain("Workspace default")
  )
})
