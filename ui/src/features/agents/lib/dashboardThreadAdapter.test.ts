import { describe, expect, it, vi } from "vitest"

import { DashboardThreadAdapter } from "./dashboardThreadAdapter"

const event = (eventId: string) => ({
  type: "event",
  event_id: eventId,
  seq: 1,
  method: "messages",
  params: {
    namespace: [],
    timestamp: 1,
    data: { event: "message-start", message_id: eventId, role: "ai" },
  },
})

describe("DashboardThreadAdapter", () => {
  it("hydrates once and replays only post-snapshot events to later filters", async () => {
    const encoder = new TextEncoder()
    let controller: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({
      start(value) {
        controller = value
      },
    })
    const request = vi.fn(async () => new Response(body, { status: 200 }))
    const adapter = new DashboardThreadAdapter(
      "http://example.test/dashboard/api",
      request as typeof fetch
    )
    adapter.setThreadId("thread-1")

    const statePromise = adapter.getState()
    controller!.enqueue(
      encoder.encode(
        `${JSON.stringify({ type: "snapshot", state: { values: { messages: [] } } })}\n`
      )
    )
    await expect(statePromise).resolves.toEqual({ values: { messages: [] } })

    controller!.enqueue(
      encoder.encode(
        `${JSON.stringify({ type: "event", event: event("live-1") })}\n`
      )
    )
    await new Promise((resolve) => setTimeout(resolve, 0))

    const first = adapter.openEventStream({ channels: ["messages"] })
    const second = adapter.openEventStream({ channels: ["messages"] })
    await expect(
      first.events[Symbol.asyncIterator]().next()
    ).resolves.toMatchObject({
      value: { event_id: "live-1" },
    })
    await expect(
      second.events[Symbol.asyncIterator]().next()
    ).resolves.toMatchObject({
      value: { event_id: "live-1" },
    })
    expect(request).toHaveBeenCalledTimes(1)

    first.close()
    second.close()
    await adapter.close()
  })
})
