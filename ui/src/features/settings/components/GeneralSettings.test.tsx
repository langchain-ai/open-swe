// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { api, type UserPreferences } from "@/lib/api"
import { GeneralSettings } from "./GeneralSettings"

vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}))

beforeEach(() => {
  vi.spyOn(api, "me").mockResolvedValue({
    login: "alice",
    email: "alice@example.com",
    avatar_url: "",
    is_admin: false,
    slack_user_id: "U123",
  })
  vi.spyOn(api, "profile").mockResolvedValue({
    personal_kitchen_channel_id: "",
  })
  vi.spyOn(api, "listSlackChannels").mockResolvedValue({
    channels: [
      {
        id: "CJOINED01",
        name: "my-kitchen",
        is_member: true,
        num_members: null,
        is_private: true,
        is_ext_shared: false,
      },
      {
        id: "COUTSIDE",
        name: "external",
        is_member: true,
        num_members: null,
        is_private: false,
        is_ext_shared: true,
      },
      {
        id: "CPENDING1",
        name: "pending-sharing",
        is_member: true,
        num_members: null,
        is_private: false,
        is_ext_shared: false,
        is_pending_ext_shared: true,
      },
      {
        id: "CNOTJOIN",
        name: "not-joined",
        is_member: false,
        num_members: null,
        is_private: false,
        is_ext_shared: false,
      },
    ],
    refreshing: false,
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("keeps preference labels when closed and saves workspace values, not labels", async () => {
  vi.mocked(api.me).mockResolvedValue({
    login: "alice",
    email: "alice@example.com",
    avatar_url: "",
    is_admin: false,
    slack_user_id: null,
  })
  let stored: UserPreferences = {
    default_visibility: "private",
    default_workspace: null,
    follow_up_behavior: "queue",
    auto_show_changes: true,
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
        kitchen_channel_ids: [],
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
      <GeneralSettings />
    </QueryClientProvider>,
    { wrapper: TooltipProvider }
  )
  const workspace = screen.getAllByRole("combobox")[2]!
  expect(
    screen
      .getByRole("combobox", { name: "Personal kitchen channel" })
      .hasAttribute("disabled")
  ).toBe(true)

  await waitFor(() => expect(workspace.hasAttribute("disabled")).toBe(false))
  expect(
    screen
      .getAllByRole("combobox")
      .slice(0, 4)
      .map((element) => element.textContent)
  ).toEqual(
    ["System", "Private · only me", "Environment default", "Queue"].map(
      (label) => expect.stringContaining(label)
    )
  )
  expect(save).not.toHaveBeenCalled()

  fireEvent.click(workspace)
  expect(
    await screen.findByRole("option", { name: "Environment default" })
  ).toBeTruthy()
  fireEvent.keyDown(workspace, { key: "Escape" })
  await waitFor(() => expect(screen.queryByRole("option")).toBeNull())
  expect(workspace.textContent).toContain("Environment default")
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
    name: "Environment default",
  })
  fireEvent.pointerDown(workspaceDefault)
  fireEvent.click(workspaceDefault)
  await waitFor(() =>
    expect(save.mock.lastCall?.[0]).toEqual(
      expect.objectContaining({ default_workspace: null })
    )
  )
  await waitFor(() =>
    expect(workspace.textContent).toContain("Environment default")
  )
})

it("optimistically selects a personal kitchen, rolls back a failed save, and clears it", async () => {
  vi.spyOn(api, "getMyPreferences").mockResolvedValue({
    default_visibility: "private",
    default_workspace: null,
    follow_up_behavior: "queue",
    auto_show_changes: true,
  })
  vi.spyOn(api, "listWorkspaceOptions").mockResolvedValue({
    default_slug: "default",
    workspaces: [],
  })
  let rejectSave: (error: Error) => void = () => {}
  const save = vi.spyOn(api, "saveProfile").mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        rejectSave = reject
      })
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <GeneralSettings />
    </QueryClientProvider>,
    { wrapper: TooltipProvider }
  )
  const kitchen = screen.getByRole("combobox", {
    name: "Personal kitchen channel",
  })
  await waitFor(() => expect(kitchen.hasAttribute("disabled")).toBe(false))
  fireEvent.focus(kitchen)
  fireEvent.click(kitchen)
  const option = await screen.findByRole("option", { name: /my-kitchen/ })
  expect(screen.queryByRole("option", { name: /external/ })).toBeNull()
  expect(screen.queryByRole("option", { name: /not-joined/ })).toBeNull()
  expect(screen.queryByRole("option", { name: /pending-sharing/ })).toBeNull()
  fireEvent.pointerDown(option)
  fireEvent.click(option)
  await waitFor(() =>
    expect(save.mock.lastCall?.[0]).toEqual(
      expect.objectContaining({ personal_kitchen_channel_id: "CJOINED01" })
    )
  )
  await waitFor(() => expect(kitchen.getAttribute("value")).toBe("#my-kitchen"))
  rejectSave(new Error("Channel is no longer available"))
  await waitFor(() => expect(kitchen.getAttribute("value")).toBe(""))

  vi.mocked(api.profile).mockResolvedValue({
    personal_kitchen_channel_id: "CJOINED01",
  })
  save.mockResolvedValueOnce({ personal_kitchen_channel_id: "CJOINED01" })
  fireEvent.click(kitchen)
  fireEvent.click(await screen.findByRole("option", { name: /my-kitchen/ }))
  await waitFor(() => expect(kitchen.getAttribute("value")).toBe("#my-kitchen"))
  await waitFor(() => expect(client.isMutating()).toBe(0))
  vi.mocked(api.profile).mockResolvedValue({ personal_kitchen_channel_id: "" })
  save.mockResolvedValueOnce({ personal_kitchen_channel_id: "" })
  fireEvent.click(screen.getByRole("button", { name: "Clear selection" }))
  await waitFor(() =>
    expect(save.mock.lastCall?.[0]).toEqual(
      expect.objectContaining({ personal_kitchen_channel_id: "" })
    )
  )
  await waitFor(() => expect(kitchen.getAttribute("value")).toBe(""))
})
