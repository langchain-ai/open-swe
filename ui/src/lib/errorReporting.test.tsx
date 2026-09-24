/** @vitest-environment jsdom */
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import { makeQueryClient } from "@/lib/query"

const toastError = vi.fn()
vi.mock("sonner", () => ({
  toast: { error: (...args: unknown[]) => toastError(...args) },
}))

const addError = vi.fn()
vi.mock("@/lib/datadog", () => ({ getDatadogRum: () => ({ addError }) }))

type Sent = { url: string; headers: Record<string, string>; body: unknown }
let sent: Array<Sent>

function stubFetch(respond: (url: string) => Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit) => {
      sent.push({
        url,
        headers: init.headers as Record<string, string>,
        body: init.body ? JSON.parse(String(init.body)) : null,
      })
      return url.endsWith("/client-errors")
        ? Promise.resolve(new Response(null, { status: 204 }))
        : respond(url)
    })
  )
}

async function runFailingMutation(meta: {
  errorTitle?: string
  silent?: boolean
}) {
  const client = makeQueryClient()
  await client
    .getMutationCache()
    .build(client, {
      mutationKey: ["threads", "pin"],
      mutationFn: () => api.saveMyInstructions("x"),
      meta,
    })
    .execute(undefined)
    .catch(() => undefined)
}

const reports = () => sent.filter((s) => s.url.endsWith("/client-errors"))

beforeEach(() => {
  sent = []
})

afterEach(() => {
  vi.unstubAllGlobals()
  toastError.mockReset()
  addError.mockReset()
})

it("shows, traces, and logs a failed mutation under the ID its request carried", async () => {
  stubFetch(async () =>
    Response.json({ detail: "Thread is archived." }, { status: 409 })
  )

  await runFailingMutation({ errorTitle: "Couldn't pin thread" })

  const requestId = sent[0]!.headers["X-Request-ID"]!
  expect(requestId).toMatch(/^req_[0-9a-f-]{36}$/)
  expect(toastError).toHaveBeenCalledWith(
    "Couldn't pin thread",
    expect.objectContaining({ id: requestId })
  )
  expect(addError).toHaveBeenCalledWith(
    expect.objectContaining({ message: "Thread is archived." }),
    expect.objectContaining({ error_id: requestId, status: 409 })
  )
  await vi.waitFor(() => expect(reports()).toHaveLength(1))
  expect(reports()[0]!.body).toMatchObject({
    error_id: requestId,
    title: "Couldn't pin thread",
    error_message: "Thread is archived.",
    status: 409,
    mutation: '["threads","pin"]',
  })
})

it("keeps the request ID when the server was never reached", async () => {
  stubFetch(() => Promise.reject(new TypeError("Failed to fetch")))

  await runFailingMutation({ errorTitle: "Couldn't pin thread" })

  const requestId = sent[0]!.headers["X-Request-ID"]
  expect(toastError).toHaveBeenCalledWith(
    "Couldn't pin thread",
    expect.objectContaining({ id: requestId })
  )
  expect(addError).toHaveBeenCalledWith(
    expect.anything(),
    expect.objectContaining({ error_id: requestId, status: 0 })
  )
})

it("still logs a silent mutation it does not toast", async () => {
  stubFetch(async () => Response.json({ detail: "bad" }, { status: 422 }))

  await runFailingMutation({ silent: true })

  const requestId = sent[0]!.headers["X-Request-ID"]
  expect(toastError).not.toHaveBeenCalled()
  expect(addError).toHaveBeenCalledWith(
    expect.anything(),
    expect.objectContaining({ error_id: requestId, status: 422 })
  )
  await vi.waitFor(() => expect(reports()).toHaveLength(1))
})
