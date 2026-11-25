/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { afterEach, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import type { ConnectResult, ManagedToolsView, SessionUser } from "@/lib/api"
import { ConnectionsPage } from "./ConnectionsPage"
import { MANAGED_TOOLS_KEY } from "./ManagedToolsSection"

vi.mock("@/lib/appCommands", () => ({ useRegisterAppCommands: () => {} }))

function makeClient(managed?: ManagedToolsView) {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  })
  client.setQueryData<ManagedToolsView>(
    MANAGED_TOOLS_KEY,
    managed ?? {
      configured: false,
      langsmith_connected: false,
      servers: [],
    }
  )
  return client
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
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
    const client = makeClient()
    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <ConnectionsPage user={user} />
        </TooltipProvider>
      </QueryClientProvider>
    )
    expect(screen.getByRole("button", { name: "Connect Slack" })).toBeTruthy()
    expect(screen.queryByRole("region", { name: "Integrations" })).toBeNull()
    expect(
      screen.queryByRole("button", { name: "Connect GitHub" }) !== null
    ).toBe(github_login === null)
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
  const client = makeClient()
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ConnectionsPage
          user={{
            login: "person",
            auth_provider: "langsmith",
            email: null,
            avatar_url: null,
            is_admin: false,
            slack_oauth_enabled: true,
          }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByRole("button", { name: "Connect Slack" }))
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
  const client = makeClient()
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ConnectionsPage
          user={{
            login: "person",
            auth_provider: "langsmith",
            email: null,
            avatar_url: null,
            is_admin: false,
            slack_oauth_enabled: true,
          }}
        />
      </TooltipProvider>
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

it("shows the Slack member ID rather than the dashboard login email", () => {
  const client = makeClient()
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ConnectionsPage
          user={{
            login: "person",
            auth_provider: "langsmith",
            email: "login@example.com",
            slack_user_id: "U12345",
            avatar_url: null,
            is_admin: false,
            slack_oauth_enabled: true,
          }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  const slack = screen.getByText("Slack").closest("tr")
  expect(slack?.textContent).toContain("U12345")
  expect(slack?.textContent).not.toContain("login@example.com")
  client.clear()
})

it("keeps accounts apart from the searchable table of integrations", async () => {
  const base = {
    enabled: true,
    has_credential: false,
    personal: false,
  }
  const client = makeClient({
    configured: true,
    langsmith_connected: true,
    servers: [
      {
        ...base,
        id: "l",
        name: "Linear",
        kind: "oauth",
        connected: true,
      },
      {
        ...base,
        id: "e",
        name: "excalidraw",
        kind: "secret",
        connected: false,
      },
      {
        ...base,
        id: "mine",
        name: "Mine",
        kind: "none",
        connected: true,
        personal: true,
      },
    ],
  })
  const removeServer = vi
    .spyOn(api, "removeManagedServer")
    .mockResolvedValue(undefined)
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ConnectionsPage
          user={{
            login: "person",
            auth_provider: "langsmith",
            github_login: "developer",
            email: "person@example.com",
            slack_user_id: "U12345",
            avatar_url: null,
            is_admin: false,
            slack_oauth_enabled: true,
          }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )

  const accounts = within(
    screen.getByRole("region", { name: "Accounts" })
  ).getByRole("table")
  expect(accounts.textContent).toContain("GitHub")
  expect(accounts.textContent).toContain("Slack")
  expect(accounts.textContent).toContain("LangSmith")
  const table = within(
    screen.getByRole("region", { name: "Integrations" })
  ).getByRole("table")
  expect(table.textContent).toContain("Linear")
  expect(table.textContent).toContain("Mine")
  expect(table.textContent).not.toContain("GitHub")
  expect(screen.getByText("3 integrations")).toBeTruthy()
  expect(
    screen.getByRole("button", { name: "Add custom integration" })
  ).toBeTruthy()
  expect(
    screen
      .getByRole("link", { name: "Connect excalidraw" })
      .getAttribute("href")
  ).toContain("/my-managed-tools/connect/e")

  fireEvent.change(screen.getByLabelText("Search integrations"), {
    target: { value: "exc" },
  })
  expect(screen.getByText("1 integration")).toBeTruthy()
  expect(table.textContent).not.toContain("Linear")
  expect(accounts.textContent).toContain("GitHub")

  fireEvent.change(screen.getByLabelText("Search integrations"), {
    target: { value: "" },
  })

  // Removing a custom connection deletes it from LangSmith, so it asks first.
  fireEvent.keyDown(screen.getByRole("button", { name: "Actions for Mine" }), {
    key: "Enter",
  })
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove" }))
  expect(await screen.findByText("Remove Mine?")).toBeTruthy()
  expect(removeServer).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Remove" }))
  await waitFor(() => expect(removeServer.mock.calls[0]?.[0]).toBe("mine"))
  client.clear()
})

it("reveals a newly added custom connection on page 1 even with 25+ connections and an active search", async () => {
  const servers = Array.from({ length: 26 }, (_, i) => ({
    id: `svc-${i}`,
    name: `Service ${String(i).padStart(2, "0")}`,
    kind: "oauth" as const,
    enabled: true,
    connected: true,
    has_credential: true,
    personal: false,
  }))
  const client = makeClient({
    configured: true,
    langsmith_connected: true,
    servers,
  })
  const added = {
    id: "custom-z",
    name: "Zeta Custom",
    kind: "oauth" as const,
    enabled: true,
    connected: false,
    has_credential: false,
    personal: true,
  }
  vi.spyOn(api, "addManagedServer").mockResolvedValue(added)
  vi.spyOn(api, "getMyManagedTools").mockResolvedValue({
    configured: true,
    langsmith_connected: true,
    servers: [...servers, added],
  })

  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ConnectionsPage
          user={{
            login: "person",
            auth_provider: "langsmith",
            github_login: "developer",
            email: "person@example.com",
            slack_user_id: "U12345",
            avatar_url: null,
            is_admin: false,
            slack_oauth_enabled: true,
          }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )

  fireEvent.change(screen.getByLabelText("Search integrations"), {
    target: { value: "Service 00" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Add custom integration" })
  )
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "Zeta Custom" },
  })
  fireEvent.change(screen.getByLabelText("MCP server URL"), {
    target: { value: "https://mcp.example.com/mcp" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Add integration" }))

  expect(
    await screen.findByRole("link", { name: "Connect Zeta Custom" })
  ).toBeTruthy()
  expect(
    (screen.getByLabelText("Search integrations") as HTMLInputElement).value
  ).toBe("")
  client.clear()
})

it("asks to sign in to LangSmith again when its grant expired", () => {
  const client = makeClient({
    configured: true,
    langsmith_connected: false,
    servers: [],
  })
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ConnectionsPage
          user={{
            login: "person",
            auth_provider: "langsmith",
            github_login: "developer",
            email: "person@example.com",
            slack_user_id: "U12345",
            avatar_url: null,
            is_admin: false,
            slack_oauth_enabled: true,
          }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  expect(
    screen.getByRole("button", { name: "Sign in to LangSmith again" })
  ).toBeTruthy()
  expect(
    screen.getByText("Sign in to LangSmith again to see your integrations.")
  ).toBeTruthy()
  client.clear()
})
