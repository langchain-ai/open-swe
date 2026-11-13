/** @vitest-environment jsdom */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"
import type { ReactNode } from "react"

import { api } from "@/lib/api"
import { MANAGED_TOOLS_KEY, useConnectPopup } from "./ManagedToolsSection"

const url = "https://smith.langchain.com/connect/service"

function setup() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  const invalidate = vi.spyOn(client, "invalidateQueries")
  const hook = renderHook(
    () =>
      useConnectPopup(MANAGED_TOOLS_KEY, (serverId) =>
        api.connectManagedTool(serverId)
      ),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    }
  )
  return { ...hook, invalidate }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("opens desktop consent in the system browser and refreshes on return", async () => {
  const openExternal = vi.fn().mockResolvedValue(true)
  vi.stubGlobal("openSweDesktop", { openExternal })
  const popup = vi.spyOn(window, "open").mockReturnValue(null)
  const connect = vi
    .spyOn(api, "connectManagedTool")
    .mockResolvedValue({ connected: false, url })
  const { result, invalidate } = setup()

  act(() => result.current.start("service"))
  await waitFor(() => expect(openExternal).toHaveBeenCalledWith(url))
  expect(connect).toHaveBeenCalledWith("service")
  expect(popup).not.toHaveBeenCalled()
  expect(result.current.connecting).toBe("service")

  act(() => window.dispatchEvent(new Event("focus")))
  expect(result.current.connecting).toBeNull()
  expect(invalidate).toHaveBeenCalledWith({ queryKey: MANAGED_TOOLS_KEY })
})

it.each([false, new Error("Browser unavailable")])(
  "allows retry when the desktop cannot open consent (%s)",
  async (failure) => {
    const openExternal = vi.fn().mockImplementation(async () => {
      if (failure instanceof Error) throw failure
      return failure
    })
    vi.stubGlobal("openSweDesktop", { openExternal })
    vi.spyOn(window, "open").mockReturnValue(null)
    vi.spyOn(api, "connectManagedTool").mockResolvedValue({
      connected: false,
      url,
    })
    const { result, invalidate } = setup()

    act(() => result.current.start("service"))
    await waitFor(() => expect(openExternal).toHaveBeenCalledWith(url))
    await waitFor(() => expect(result.current.connecting).toBeNull())
    act(() => window.dispatchEvent(new Event("focus")))
    expect(invalidate).not.toHaveBeenCalled()
  }
)

it("leaves web consent to the button's link and refreshes on return", () => {
  // Some browsers lose a tab a script opened before it is sent on, so the web never
  // opens one: the Connect link itself goes to Robin's connect route.
  const popup = vi.spyOn(window, "open")
  const connect = vi.spyOn(api, "connectManagedTool")
  const { result, invalidate } = setup()

  act(() => result.current.start("service"))
  expect(popup).not.toHaveBeenCalled()
  expect(connect).not.toHaveBeenCalled()
  expect(result.current.connecting).toBe("service")

  act(() => window.dispatchEvent(new Event("focus")))
  expect(result.current.connecting).toBeNull()
  expect(invalidate).toHaveBeenCalledWith({ queryKey: MANAGED_TOOLS_KEY })
})
