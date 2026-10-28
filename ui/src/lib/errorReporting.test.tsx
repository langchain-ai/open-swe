/** @vitest-environment jsdom */
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { render, screen } from "@testing-library/react"

import { ErrorToastBody } from "@/lib/errorReporting"
import { api } from "@/lib/api"
import { agentsApi } from "@/features/agents/lib/api"
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

async function runFailingMutation(
  meta: { errorTitle?: string; silent?: boolean },
  mutationFn: () => Promise<unknown> = () => api.saveMyInstructions("x")
) {
  const client = makeQueryClient()
  await client
    .getMutationCache()
    .build(client, {
      mutationKey: ["threads", "pin"],
      mutationFn,
      meta,
    })
    .execute(undefined)
    .catch(() => undefined)
}

it("links the existing Slack review card in a refusal", () => {
  const permalink = "https://example.slack.com/archives/C123/p1791222470078909"
  render(
    <ErrorToastBody
      message={`Dismiss the existing card. Open in Slack: ${permalink}`}
      id="req_test"
    />
  )
  expect(
    screen
      .getByRole("link", { name: "Open review card in Slack" })
      .getAttribute("href")
  ).toBe(permalink)
})

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

it("formats validation errors without exposing the rejected input in the toast", async () => {
  stubFetch(async () =>
    Response.json(
      {
        detail: [
          {
            type: "string_too_long",
            loc: ["body", "name"],
            msg: "String should have at most 120 characters",
            input: "private automation instructions".repeat(10),
            ctx: { max_length: 120 },
          },
          { loc: ["body", "prompt"], msg: "Field required" },
        ],
      },
      { status: 422 }
    )
  )

  await runFailingMutation({ errorTitle: "Couldn't create automation" }, () =>
    agentsApi.createSchedule({
      name: "private automation instructions".repeat(10),
      prompt: "Check dependencies",
      workspace: "default",
    })
  )

  const options = toastError.mock.calls[0]![1] as {
    description: React.ReactElement
  }
  const { container } = render(options.description)
  expect(container.textContent).toContain(
    "Please use a shorter name (120 characters or fewer); prompt: Field required"
  )
  expect(container.textContent).not.toContain("private automation instructions")
  expect(container.textContent).not.toContain("string_too_long")
  expect(container.textContent).toContain(sent[0]!.headers["X-Request-ID"])
})

it("trims an oversized error so the server still logs it", async () => {
  stubFetch(async () =>
    Response.json({ detail: "x".repeat(5000) }, { status: 422 })
  )

  await runFailingMutation({ errorTitle: "Couldn't pin thread" })

  await vi.waitFor(() => expect(reports()).toHaveLength(1))
  const body = reports()[0]!.body as { error_message: string }
  expect(body.error_message).toHaveLength(2000)
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
