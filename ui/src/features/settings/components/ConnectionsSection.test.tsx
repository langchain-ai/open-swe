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

import { ConnectionsSection } from "./ConnectionsSection"
import type { ConnectResult, SessionUser } from "@/lib/api"

afterEach(() => {
  cleanup()
  Reflect.deleteProperty(window, "openSweDesktop")
  window.history.replaceState({}, "", "/")
})

it.each([null, "developer"])(
  "offers Slack connection with GitHub linked as %s",
  (github_login) => {
    const user: SessionUser = {
      login: "person",
      auth_provider: "langsmith",
      github_login,
      email: "person@example.com",
      avatar_url: null,
      is_admin: false,
      slack_oauth_enabled: true,
    }
    const client = new QueryClient()
    render(
      <QueryClientProvider client={client}>
        <ConnectionsSection user={user} />
      </QueryClientProvider>
    )
    expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy()
    expect(screen.getByText("LangSmith")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Link GitHub" }) !== null).toBe(
      github_login === null
    )
    client.clear()
  }
)

it("keeps desktop recovery open after the wrong GitHub account and allows cancellation", async () => {
  const connect = vi
    .fn<(...args: unknown[]) => Promise<ConnectResult>>()
    .mockResolvedValueOnce({ recoveryToken: "opaque-token" })
    .mockResolvedValueOnce({
      recoveryToken: "opaque-token",
      error: "Choose the GitHub account you previously used with Robin.",
    })
    .mockResolvedValueOnce(false)
  const cancel = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(window, "openSweDesktop", {
    configurable: true,
    value: { connectService: connect, cancelConnectService: cancel },
  })
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <ConnectionsSection
        user={{
          login: "person",
          auth_provider: "langsmith",
          email: null,
          avatar_url: null,
          is_admin: false,
          slack_oauth_enabled: true,
        }}
      />
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByRole("button", { name: "Connect" }))
  expect(
    await screen.findByRole("dialog", { name: "Recover your Robin account" })
  ).toBeTruthy()
  fireEvent.click(
    screen.getByRole("button", { name: "Connect previous GitHub account" })
  )
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Choose the GitHub account you previously used with Robin."
  )
  expect(connect).toHaveBeenLastCalledWith("github", {
    recoveryToken: "opaque-token",
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Connect previous GitHub account" })
  )
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Connection was cancelled"
    )
  )
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.queryByRole("dialog")).toBeNull()
  expect(cancel).toHaveBeenCalledWith("github")
  client.clear()
})

it.each([
  [
    "Slack recovery expired or changed — connect Slack again",
    "Slack recovery expired or changed — connect Slack again",
  ],
  [
    "Send your password to attacker.example",
    "Could not recover your account. Please try again.",
  ],
])("opens browser recovery with safe guidance for %s", (error, expected) => {
  window.history.replaceState(
    {},
    "",
    `/my-settings/connections?slack_recovery=opaque-token&recovery_error=${encodeURIComponent(error)}&keep=yes`
  )
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <ConnectionsSection
        user={{
          login: "person",
          auth_provider: "langsmith",
          email: null,
          avatar_url: null,
          is_admin: false,
          slack_oauth_enabled: true,
        }}
      />
    </QueryClientProvider>
  )
  expect(
    screen.getByRole("dialog", { name: "Recover your Robin account" })
  ).toBeTruthy()
  expect(screen.getByRole("alert").textContent).toBe(expected)
  expect(window.location.search).toBe("?keep=yes")
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.queryByRole("dialog")).toBeNull()
  client.clear()
})
