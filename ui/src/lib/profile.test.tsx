/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { api, type Profile, type ProfileUpdate } from "./api"
import { usePatchProfile, useProfile } from "./profile"

vi.mock("./session", () => ({
  useSession: () => ({ data: { login: "alice" } }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

interface Pending {
  body: ProfileUpdate
  resolve: () => void
  reject: (error: Error) => void
}

function mockServer() {
  let stored: Profile = {
    login: "alice",
    default_model: "openai:test",
    reasoning_effort: "medium",
    draft_prs: true,
    review_draft_prs: null,
  }
  const requests: Array<Pending> = []
  vi.spyOn(api, "profile").mockImplementation(async () => stored)
  vi.spyOn(api, "saveProfile").mockImplementation(
    (body) =>
      new Promise<Profile>((resolve, reject) =>
        requests.push({
          body,
          resolve: () => {
            stored = {
              ...body,
              login: "alice",
              model_routing_enabled: body.model_routing_enabled ?? undefined,
            }
            resolve(stored)
          },
          reject,
        })
      )
  )
  return requests
}

function renderTwoPages() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return renderHook(
    () => ({
      profile: useProfile(),
      a: usePatchProfile(),
      b: usePatchProfile(),
    }),
    { wrapper }
  )
}

it("chains a second page's save behind the first and keeps both fields", async () => {
  const requests = mockServer()
  const { result } = renderTwoPages()
  await waitFor(() => expect(result.current.profile.data).toBeDefined())

  act(() => {
    result.current.a.patch({ draft_prs: false }, "m", "e")
    result.current.b.patch({ review_draft_prs: true }, "m", "e")
  })

  await waitFor(() => expect(requests).toHaveLength(1))
  expect(result.current.profile.data).toMatchObject({
    draft_prs: false,
    review_draft_prs: true,
  })

  act(() => requests[0]!.resolve())
  await waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1]!.body).toMatchObject({
    draft_prs: false,
    review_draft_prs: true,
  })

  act(() => requests[1]!.resolve())
  await waitFor(() =>
    expect(result.current.profile.data).toMatchObject({
      draft_prs: false,
      review_draft_prs: true,
    })
  )
})

it("does not resend a failed save's field with the next one", async () => {
  const requests = mockServer()
  const { result } = renderTwoPages()
  await waitFor(() => expect(result.current.profile.data).toBeDefined())

  act(() => {
    result.current.a.patch({ draft_prs: false }, "m", "e")
    result.current.b.patch({ review_draft_prs: true }, "m", "e")
  })
  await waitFor(() => expect(requests).toHaveLength(1))

  act(() => requests[0]!.reject(new Error("nope")))
  await waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1]!.body).toMatchObject({
    draft_prs: true,
    review_draft_prs: true,
  })
  expect(result.current.profile.data?.draft_prs).toBe(true)

  act(() => requests[1]!.resolve())
  await waitFor(() =>
    expect(result.current.profile.data).toMatchObject({
      draft_prs: true,
      review_draft_prs: true,
    })
  )
})
