import { expect, it, vi } from "vitest"
import { createRunAcceptanceTracker } from "./runAcceptance"

const STREAM = "http://localhost/dashboard/api/threads/one/runs/stream"
const CREATE = "http://localhost/dashboard/api/threads/one/runs"

function body(id: string | null) {
  return JSON.stringify({
    input: id === null ? {} : { messages: [{ id }] },
    stream_mode: ["values"],
  })
}

/** A run's stream as `runs/stream` answers it: open unless `ended`, events still to come. */
function streamed(
  headers: Record<string, string>,
  events: Array<string> = [],
  { ended = false } = {}
): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const event of events) controller.enqueue(encoder.encode(event))
        if (ended) controller.close()
      },
    }),
    { headers: { "content-type": "text/event-stream", ...headers } }
  )
}

const RUN_LOCATION = { "content-location": "/threads/one/runs/run-1" }

it("isolates overlapping submissions and ignores untracked requests", async () => {
  const fetcher = vi.fn<typeof fetch>()
  const tracker = createRunAcceptanceTracker(fetcher)
  const send = (id: string) =>
    tracker.fetch(STREAM, { method: "POST", body: body(id) })
  fetcher.mockResolvedValueOnce(streamed(RUN_LOCATION))
  await send("untracked")
  expect(await tracker.track("untracked")()).toEqual({ accepted: false })
  const first = tracker.track("first")
  const second = tracker.track("second")
  fetcher
    .mockResolvedValueOnce(streamed(RUN_LOCATION))
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
  await Promise.all([send("first"), send("second")])
  expect(await first()).toEqual({ accepted: true })
  expect(await second()).toEqual({ accepted: false })
  expect(await tracker.track("first")()).toEqual({ accepted: false })
})

it("takes a streamed run from its first metadata event when the response names none", async () => {
  const tracker = createRunAcceptanceTracker(
    vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        streamed(
          {},
          [
            'event: metadata\ndata: {"run_id": "run-1", "attempt": 1}\n\n',
            "event: values\ndata: {}\n\n",
          ],
          { ended: true }
        )
      )
  )
  const accepted = tracker.track("message")
  const response = await tracker.fetch(STREAM, {
    method: "POST",
    body: body("message"),
  })
  expect(await accepted()).toEqual({ accepted: true })
  // The SDK still reads the whole stream.
  expect(await new Response(response.body).text()).toContain("event: values")
})

it("accepts a message steered into a run with nothing to join", async () => {
  const tracker = createRunAcceptanceTracker(
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response("", {
        headers: {
          "content-type": "text/event-stream",
          "x-robin-steered": "true",
        },
      })
    )
  )
  const accepted = tracker.track("message")
  await tracker.fetch(STREAM, { method: "POST", body: body("message") })
  expect(await accepted()).toEqual({ accepted: true })
})

it("reports a message-less run's acceptance as soon as it starts, before its stream ends", async () => {
  const tracker = createRunAcceptanceTracker(
    vi.fn<typeof fetch>().mockResolvedValue(streamed(RUN_LOCATION))
  )
  const onAccepted = vi.fn()
  const onRejected = vi.fn()
  const settled = tracker.track(null, onAccepted, onRejected)
  await tracker.fetch(STREAM, { method: "POST", body: body(null) })
  await vi.waitFor(() => expect(onAccepted).toHaveBeenCalledOnce())
  expect(onRejected).not.toHaveBeenCalled()
  expect(await settled()).toEqual({ accepted: true })
})

it.each([
  ["a run", Response.json({ run_id: "run-1" }), true],
  [
    "a steer with nothing to join",
    new Response(null, { status: 202, headers: { "x-robin-steered": "true" } }),
    true,
  ],
  ["no run", new Response(null, { status: 202 }), false],
  ["no content", new Response(null, { status: 204 }), false],
  ["a server error", new Response(null, { status: 503 }), false],
])(
  "accepts a created run only when the server answers with %s",
  async (_label, response, accepted) => {
    const tracker = createRunAcceptanceTracker(
      vi.fn<typeof fetch>().mockResolvedValue(response)
    )
    const settled = tracker.track("message")
    await tracker.fetch(CREATE, { method: "POST", body: body("message") })
    expect(await settled()).toEqual({ accepted })
  }
)

it.each([STREAM, CREATE])(
  "keeps the reason the server gave for refusing a start on %s",
  async (url) => {
    const detail = "This thread is read-only."
    const tracker = createRunAcceptanceTracker(
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(Response.json({ detail }, { status: 410 }))
    )
    const accepted = tracker.track("message")
    await tracker.fetch(url, { method: "POST", body: body("message") })
    const acceptance = await accepted()
    expect(acceptance.accepted).toBe(false)
    expect(!acceptance.accepted && acceptance.refusal).toMatchObject({
      status: 410,
      message: detail,
    })
  }
)
