import { expect, it, vi } from "vitest"
import { createRunAcceptanceTracker } from "./runAcceptance"

it("isolates overlapping submissions and ignores untracked requests", async () => {
  const fetcher = vi.fn<typeof fetch>()
  const tracker = createRunAcceptanceTracker(fetcher)
  const send = (id: string) =>
    tracker.fetch("http://localhost/threads/one/commands", {
      method: "POST",
      body: JSON.stringify({
        method: "run.start",
        params: { input: { messages: [{ id }] } },
      }),
    })
  const success = () =>
    Response.json({ type: "success", result: { run_id: "run" } })
  fetcher.mockResolvedValueOnce(success())
  await send("untracked")
  expect(await tracker.track("untracked")()).toEqual({ accepted: false })
  const first = tracker.track("first")
  const second = tracker.track("second")
  fetcher
    .mockResolvedValueOnce(success())
    .mockResolvedValueOnce(Response.json({ type: "error" }))
  await Promise.all([send("first"), send("second")])
  expect(await first()).toEqual({ accepted: true })
  expect(await second()).toEqual({ accepted: false })
  expect(await tracker.track("first")()).toEqual({ accepted: false })
})

it.each([202, 204, 503])(
  "does not treat an HTTP %s response as run creation",
  async (status) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status }))
    const tracker = createRunAcceptanceTracker(fetcher)
    const accepted = tracker.track("message")
    await tracker.fetch("http://localhost/threads/one/runs", {
      method: "POST",
      body: JSON.stringify({ input: { messages: [{ id: "message" }] } }),
    })
    expect(await accepted()).toEqual({ accepted: false })
  }
)

it("reports offload acceptance as soon as the command responds, before the stream settles", async () => {
  const tracker = createRunAcceptanceTracker(
    vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json({ type: "success", result: { run_id: "offload-run" } })
      )
  )
  const onAccepted = vi.fn()
  const onRejected = vi.fn()
  const settled = tracker.track(null, onAccepted, onRejected)
  await tracker.fetch("http://localhost/threads/one/commands", {
    method: "POST",
    body: JSON.stringify({ method: "run.start", params: { input: null } }),
  })
  await vi.waitFor(() => expect(onAccepted).toHaveBeenCalledOnce())
  expect(onRejected).not.toHaveBeenCalled()
  expect(await settled()).toEqual({ accepted: true })
  expect(onAccepted).toHaveBeenCalledOnce()
})

it("reports protocol rejection even when the HTTP request succeeds", async () => {
  const tracker = createRunAcceptanceTracker(
    vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json({ type: "error", message: "private error" })
      )
  )
  const onAccepted = vi.fn()
  const onRejected = vi.fn()
  const settled = tracker.track("message", onAccepted, onRejected)
  await tracker.fetch("http://localhost/threads/one/commands", {
    method: "POST",
    body: JSON.stringify({
      method: "run.start",
      params: { input: { messages: [{ id: "message" }] } },
    }),
  })
  await vi.waitFor(() => expect(onRejected).toHaveBeenCalledOnce())
  expect(onAccepted).not.toHaveBeenCalled()
  expect(await settled()).toEqual({ accepted: false })
})

it("keeps the reason the server gave for refusing a start", async () => {
  const detail = "This thread is read-only."
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ detail }, { status: 410 }))
  const tracker = createRunAcceptanceTracker(fetcher)
  const accepted = tracker.track("message")
  await tracker.fetch("http://localhost/threads/one/commands", {
    method: "POST",
    body: JSON.stringify({
      method: "run.start",
      params: { input: { messages: [{ id: "message" }] } },
    }),
  })
  const acceptance = await accepted()
  expect(acceptance.accepted).toBe(false)
  expect(!acceptance.accepted && acceptance.refusal).toMatchObject({
    status: 410,
    message: detail,
  })
})
