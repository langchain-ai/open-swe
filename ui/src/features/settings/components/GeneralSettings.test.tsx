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
import { afterEach, expect, it, vi } from "vitest"

import { api, type UserPreferences } from "@/lib/api"
import { GeneralSettings } from "./GeneralSettings"

vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("saves follow-up behavior without changing other preferences", async () => {
  let stored: UserPreferences = {
    default_visibility: "private",
    follow_up_behavior: "queue",
    auto_show_changes: true,
  }
  vi.spyOn(api, "getMyPreferences").mockImplementation(async () => stored)
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
  const followUp = screen.getByRole("combobox", { name: "Follow-up behavior" })
  await waitFor(() => expect(followUp.hasAttribute("disabled")).toBe(false))
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(followUp)
  const steer = await screen.findByRole("option", { name: "Steer" })
  fireEvent.pointerDown(steer)
  fireEvent.click(steer)
  await waitFor(() =>
    expect(save.mock.lastCall?.[0]).toEqual({
      default_visibility: "private",
      follow_up_behavior: "steer",
      auto_show_changes: true,
    })
  )
})
