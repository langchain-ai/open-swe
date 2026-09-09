import { expect, it, vi } from "vitest"
import { Client } from "@langchain/langgraph-sdk"
import { threadStreamFetch } from "./threadStreamFetch"

it("reconnects through the real SDK transport after a successful response ends", async () => {
  const fetchMock = vi.fn<typeof fetch>(
    async () =>
      new Response("data: {}\n\n", {
        headers: { "content-type": "text/event-stream" },
      })
  )
  const client = new Client({ apiUrl: "http://runtime.invalid", apiKey: null })
  const thread = client.threads.stream("thread", {
    assistantId: "agent",
    fetch: threadStreamFetch(fetchMock, () => {}),
    maxReconnectAttempts: 1,
    reconnectDelayMs: () => 0,
  })
  const sub = await thread.subscribe({ channels: ["lifecycle"] })
  try {
    for await (const _ of sub) {
      /* Drain the subscription through reconnect. */
    }
  } catch {
    /* Retry exhaustion is expected. */
  }
  expect(fetchMock.mock.calls.length).toBeGreaterThan(1)
  await thread.close()
})
