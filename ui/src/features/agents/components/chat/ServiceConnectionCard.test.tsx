/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import { ServiceConnectionCard } from "./ServiceConnectionCard"

const clients: QueryClient[] = []

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  render(
    <QueryClientProvider client={client}>
      <ServiceConnectionCard />
    </QueryClientProvider>
  )
}

afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  delete window.openSweDesktop
  vi.restoreAllMocks()
})

it("requires a click and does not treat cancelled desktop consent as connected", async () => {
  vi.spyOn(api, "getMyNotionStatus").mockResolvedValue({ connected: false })
  const connect = vi.fn().mockResolvedValue(false)
  Object.defineProperty(window, "openSweDesktop", {
    configurable: true,
    value: { connectService: connect },
  })
  renderCard()
  const button = await screen.findByRole("button", { name: "Connect Notion" })
  expect(connect).not.toHaveBeenCalled()
  fireEvent.click(button)
  await screen.findByText("Connection wasn't completed. You can try again.")
  expect(connect).toHaveBeenCalledWith("notion")
  expect(screen.queryByText("Connected to your account")).toBeNull()
  expect((button as HTMLButtonElement).disabled).toBe(false)
})

it("opens consent separately, stays retryable, and refreshes on return", async () => {
  const status = vi
    .spyOn(api, "getMyNotionStatus")
    .mockResolvedValue({ connected: false })
  const open = vi.spyOn(window, "open").mockReturnValue(null)
  const chatUrl = window.location.href
  renderCard()
  const button = await screen.findByRole("button", { name: "Connect Notion" })
  expect(open).not.toHaveBeenCalled()
  fireEvent.click(button)
  expect((button as HTMLButtonElement).disabled).toBe(false)
  expect(screen.queryByText("Connected to your account")).toBeNull()
  expect(window.location.href).toBe(chatUrl)
  fireEvent.click(button)
  expect(open).toHaveBeenCalledTimes(2)
  expect(open).toHaveBeenLastCalledWith(
    `/dashboard/api/notion/login?${new URLSearchParams({ redirect_to: chatUrl })}`,
    "_blank",
    "noopener,noreferrer"
  )
  status.mockResolvedValue({ connected: true })
  fireEvent.focus(window)
  await screen.findByText("Connected to your account")
})

it("shows connected only after reading the current viewer's persisted status", async () => {
  vi.spyOn(api, "getMyNotionStatus")
    .mockResolvedValueOnce({ connected: false })
    .mockResolvedValue({ connected: true })
  Object.defineProperty(window, "openSweDesktop", {
    configurable: true,
    value: { connectService: vi.fn().mockResolvedValue(true) },
  })
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "Connect Notion" }))
  await screen.findByText("Connected to your account")
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Reconnect Notion" })
    ).toBeTruthy()
  )
})
