/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SidebarUserMenu } from "./SidebarUserMenu"

const mocks = vi.hoisted(() => ({
  datadogInitialized: false,
  datadogSessionLink: undefined as string | undefined,
  navigate: vi.fn(),
  setTheme: vi.fn(),
  writeText: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => mocks.navigate,
}))
vi.mock("@/lib/datadog", () => ({
  getDatadogSessionLink: () => mocks.datadogSessionLink,
  isDatadogRumInitialized: () => mocks.datadogInitialized,
  subscribeToDatadogInitialization: () => () => {},
}))
vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ theme: "system", setTheme: mocks.setTheme }),
}))

function renderMenu(isAdmin = false) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SidebarUserMenu
        user={{
          login: "octocat",
          email: "octocat@example.com",
          avatar_url: null,
          is_admin: isAdmin,
        }}
      />
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByRole("button", { name: /octocat/i }))
}

afterEach(() => {
  cleanup()
  mocks.datadogInitialized = false
  mocks.datadogSessionLink = undefined
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

describe("SidebarUserMenu", () => {
  it("copies the current Datadog session link", async () => {
    mocks.datadogInitialized = true
    mocks.datadogSessionLink =
      "https://app.datadoghq.com/rum/explorer?query=session"
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: mocks.writeText },
    })
    mocks.writeText.mockResolvedValue(undefined)

    renderMenu()
    fireEvent.click(screen.getByRole("menuitem", { name: "Report an issue" }))
    fireEvent.click(screen.getByRole("button", { name: "Copy session link" }))

    await waitFor(() => {
      expect(mocks.writeText).toHaveBeenCalledWith(mocks.datadogSessionLink)
      expect(screen.getByRole("status").textContent).toContain(
        "Session link copied"
      )
    })
  })

  it("shows a failure state when clipboard access is denied", async () => {
    mocks.datadogInitialized = true
    mocks.datadogSessionLink =
      "https://app.datadoghq.com/rum/explorer?query=session"
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: mocks.writeText },
    })
    mocks.writeText.mockRejectedValue(new Error("denied"))

    renderMenu()
    fireEvent.click(screen.getByRole("menuitem", { name: "Report an issue" }))
    fireEvent.click(screen.getByRole("button", { name: "Copy session link" }))

    expect(
      await screen.findByText(/Couldn't copy the session link/)
    ).toBeTruthy()
  })

  it("shows a failure state when RUM has no current session", async () => {
    mocks.datadogInitialized = true
    renderMenu()

    fireEvent.click(screen.getByRole("menuitem", { name: "Report an issue" }))
    fireEvent.click(screen.getByRole("button", { name: "Copy session link" }))

    expect(
      await screen.findByText(/Couldn't copy the session link/)
    ).toBeTruthy()
  })

  it("offers reporting without a session replay when RUM is unavailable", () => {
    renderMenu()
    fireEvent.click(screen.getByRole("menuitem", { name: "Report an issue" }))

    expect(screen.getByRole("dialog")).toBeTruthy()
    expect(
      screen.getByText(/A session replay link is unavailable/)
    ).toBeTruthy()
    expect(
      screen.queryByRole("button", { name: "Copy session link" })
    ).toBeNull()
  })
})
