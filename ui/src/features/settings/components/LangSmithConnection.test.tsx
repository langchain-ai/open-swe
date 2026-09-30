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

import { api } from "@/lib/api"
import type { LangSmithCredentialStatus } from "@/lib/api"
import { LangSmithConnection } from "./LangSmithConnection"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("validates before showing connected and clears the key when closed", async () => {
  const disconnected: LangSmithCredentialStatus = {
    connected: false,
    method: null,
    region: "us",
    email: null,
    name: null,
    workspace_id: null,
    reconnect_required: false,
  }
  vi.spyOn(api, "getMyLangSmithStatus").mockResolvedValue(disconnected)
  let finish: (value: LangSmithCredentialStatus) => void = () => {}
  const save = vi.spyOn(api, "connectLangSmithKey").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <LangSmithConnection />
    </QueryClientProvider>
  )
  const connect = screen.getByRole("button", { name: "Connect" })
  await waitFor(() => expect(connect.hasAttribute("disabled")).toBe(false))
  fireEvent.click(connect)
  fireEvent.click(screen.getByRole("button", { name: "Use API key instead" }))
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "discard-me" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  fireEvent.click(connect)
  fireEvent.click(screen.getByRole("button", { name: "Use API key instead" }))
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("")
  fireEvent.change(screen.getByLabelText("LangSmith region"), {
    target: { value: "eu" },
  })
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "test-secret" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save API key" }))
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      { region: "eu", api_key: "test-secret", workspace_id: null },
      expect.anything()
    )
  )
  expect(screen.getByText("Not connected")).toBeTruthy()
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("")
  finish({ ...disconnected, connected: true, method: "api_key", region: "eu" })
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  expect(screen.getByText(/Connected with an API key · EU/)).toBeTruthy()
  client.clear()
})
