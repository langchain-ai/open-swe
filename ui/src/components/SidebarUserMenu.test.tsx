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

function renderMenu() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SidebarUserMenu
        user={{
          login: "octocat",
          email: "octocat@example.com",
          avatar_url: null,
          is_admin: false,
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
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy Datadog link" }))

    await waitFor(() => {
      expect(mocks.writeText).toHaveBeenCalledWith(mocks.datadogSessionLink)
      expect(
        screen.getByRole("menuitem", { name: "Copied Datadog link" })
      ).toBeTruthy()
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
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy Datadog link" }))

    expect(
      await screen.findByRole("menuitem", {
        name: "Couldn't copy Datadog link",
      })
    ).toBeTruthy()
  })

  it("shows a failure state when RUM has no current session", async () => {
    mocks.datadogInitialized = true
    renderMenu()

    fireEvent.click(screen.getByRole("menuitem", { name: "Copy Datadog link" }))

    expect(
      await screen.findByRole("menuitem", {
        name: "Couldn't copy Datadog link",
      })
    ).toBeTruthy()
  })

  it("hides the item before RUM initializes", () => {
    renderMenu()

    expect(screen.queryByRole("menuitem", { name: /Datadog link/ })).toBeNull()
  })
})
