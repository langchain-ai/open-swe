/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, expect, it, vi } from "vitest"

import {
  api,
  type WorkspaceSettings,
  type WorkspaceSettingsOverrides,
  type WorkspaceSettingsView,
} from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import { makeQueryClient } from "@/lib/query"
import { INSTANCE_SCOPE, useScopedSettings } from "./settingsScope"

vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))

const BASE: WorkspaceSettings = {
  review_draft_prs: false,
  pr_summaries: false,
  review_trace_links: false,
  fable_enabled: false,
}
const SCOPE = { kind: "workspace", slug: "oss" } as const

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

interface Pending {
  overrides: WorkspaceSettingsOverrides
  resolve: () => void
  reject: (error: Error) => void
}

function mockWorkspaceServer() {
  let stored: WorkspaceSettingsView = { effective: BASE, overrides: {} }
  const requests: Array<Pending> = []
  vi.spyOn(api, "getWorkspaceSettings").mockImplementation(async () => stored)
  vi.spyOn(api, "saveWorkspaceSettings").mockImplementation(
    (_slug, overrides) =>
      new Promise<WorkspaceSettingsView>((resolve, reject) =>
        requests.push({
          overrides,
          resolve: () => {
            stored = { effective: { ...BASE, ...overrides }, overrides }
            resolve(stored)
          },
          reject,
        })
      )
  )
  return requests
}

function renderTwoSections(client: QueryClient) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return renderHook(
    () => ({ a: useScopedSettings(SCOPE), b: useScopedSettings(SCOPE) }),
    { wrapper }
  )
}

function newClient() {
  return makeQueryClient()
}

it("sends a second section's save after the first and keeps both patches", async () => {
  const requests = mockWorkspaceServer()
  const client = newClient()
  const { result } = renderTwoSections(client)
  await waitFor(() => expect(result.current.a.data).toBeDefined())

  act(() => result.current.a.save({ fable_enabled: true }))
  act(() => result.current.b.save({ pr_summaries: true }))

  await waitFor(() => expect(requests).toHaveLength(1))
  expect(result.current.a.data).toMatchObject({
    fable_enabled: true,
    pr_summaries: true,
  })
  expect(result.current.b.inherits("fable_enabled")).toBe(false)

  act(() => requests[0]!.resolve())
  await waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1]!.overrides).toEqual({
    fable_enabled: true,
    pr_summaries: true,
  })

  act(() => requests[1]!.resolve())
  await waitFor(() =>
    expect(client.getQueryData(["workspaceSettings", "oss"])).toEqual({
      effective: { ...BASE, fable_enabled: true, pr_summaries: true },
      overrides: { fable_enabled: true, pr_summaries: true },
    })
  )
})

it("drops only the failed patch and reports the failure", async () => {
  const requests = mockWorkspaceServer()
  const client = newClient()
  const { result } = renderTwoSections(client)
  await waitFor(() => expect(result.current.a.data).toBeDefined())

  act(() => result.current.a.save({ fable_enabled: true }))
  act(() => result.current.b.save({ pr_summaries: true }))
  await waitFor(() => expect(requests).toHaveLength(1))

  act(() => requests[0]!.reject(new Error("forbidden")))
  await waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1]!.overrides).toEqual({ pr_summaries: true })
  expect(reportError).toHaveBeenCalledWith(
    expect.objectContaining({
      title: "Couldn't save settings",
      error: expect.objectContaining({ message: "forbidden" }),
    })
  )
  expect(result.current.a.data?.fable_enabled).toBe(false)
  expect(result.current.a.data?.pr_summaries).toBe(true)

  act(() => requests[1]!.resolve())
  await waitFor(() =>
    expect(client.getQueryData(["workspaceSettings", "oss"])).toEqual({
      effective: { ...BASE, pr_summaries: true },
      overrides: { pr_summaries: true },
    })
  )
})

it("refreshes workspace settings after an instance save", async () => {
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(BASE)
  vi.spyOn(api, "saveInstanceSettings").mockImplementation(async (body) => body)
  const client = newClient()
  client.setQueryData(["workspaceSettings", "oss"], {
    effective: BASE,
    overrides: {},
  })
  const { result } = renderHook(() => useScopedSettings(INSTANCE_SCOPE), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  await waitFor(() => expect(result.current.data).toBeDefined())

  act(() => result.current.save({ fable_enabled: true }))

  await waitFor(() =>
    expect(
      client.getQueryState(["workspaceSettings", "oss"])?.isInvalidated
    ).toBe(true)
  )
  expect(api.saveInstanceSettings).toHaveBeenCalledWith({
    ...BASE,
    fable_enabled: true,
  })
})
