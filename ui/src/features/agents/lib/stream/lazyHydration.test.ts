/** @vitest-environment jsdom */

import { ToolMessage } from "@langchain/core/messages"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Client } from "@langchain/langgraph-sdk"

import {
  LAZY_MARKER_KEY,
  lazyHydrationEnabled,
  lazyMarker,
  markTranscriptPainted,
  useLazyToolResults,
  withLazyHydration,
} from "./lazyHydration"

const API = "http://localhost:2024/dashboard/api"
const THREAD = "11111111-2222-3333-4444-555555555555"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function fakeClient() {
  const original = vi.fn(async () => ({ values: { messages: ["full"] } }))
  const client = { threads: { getState: original } } as unknown as Client
  return { client, original }
}

async function flush(): Promise<void> {
  await new Promise((done) => setTimeout(done, 0))
}

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState(null, "", "/agents")
  useLazyToolResults.setState({ results: {}, loading: {} })
})

afterEach(() => {
  vi.useRealTimers()
})

describe("lazyHydrationEnabled", () => {
  it("is on by default and toggled by the lazy query flag, persistently", () => {
    expect(lazyHydrationEnabled()).toBe(true)
    window.history.replaceState(null, "", "/agents?lazy=0")
    expect(lazyHydrationEnabled()).toBe(false)
    window.history.replaceState(null, "", "/agents")
    expect(lazyHydrationEnabled()).toBe(false)
    window.history.replaceState(null, "", "/agents?lazy=1")
    expect(lazyHydrationEnabled()).toBe(true)
  })
})

describe("withLazyHydration", () => {
  it("hydrates from the skeleton and applies tool results after first paint", async () => {
    const { client } = fakeClient()
    const calls: Array<string> = []
    const fetchImpl: typeof fetch = async (input) => {
      const url = String(input)
      calls.push(url)
      if (url.endsWith("/state?view=skeleton")) {
        return jsonResponse({
          values: { messages: [] },
          [LAZY_MARKER_KEY]: { deferred: 1, deferred_bytes: 4096 },
        })
      }
      if (url.endsWith("/state/tool-results")) {
        return jsonResponse({
          results: {
            call_1: {
              content: "the whole output",
              artifact: null,
              status: "success",
            },
          },
        })
      }
      throw new Error(`unexpected ${url}`)
    }
    withLazyHydration(client, API, fetchImpl)

    const state = await client.threads.getState(THREAD)
    expect(state).toMatchObject({ values: { messages: [] } })
    expect(calls[0]).toBe(`${API}/threads/${THREAD}/state?view=skeleton`)

    await flush()
    expect(calls[1]).toBe(`${API}/threads/${THREAD}/state/tool-results`)
    // Results wait for the transcript's first frame.
    expect(useLazyToolResults.getState().results[THREAD]).toBeUndefined()
    markTranscriptPainted(THREAD)
    await flush()
    expect(useLazyToolResults.getState().results[THREAD]).toEqual({
      call_1: {
        content: "the whole output",
        artifact: null,
        status: "success",
      },
    })
    expect(useLazyToolResults.getState().loading[THREAD]).toBe(false)
  })

  it("skips the results fetch when nothing was deferred", async () => {
    const { client } = fakeClient()
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        values: { messages: [] },
        [LAZY_MARKER_KEY]: { deferred: 0, deferred_bytes: 0 },
      })
    )
    withLazyHydration(client, API, fetchImpl as unknown as typeof fetch)
    await client.threads.getState(THREAD)
    await flush()
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it("falls through to the SDK when the flag is off or a checkpoint is requested", async () => {
    const { client, original } = fakeClient()
    const fetchImpl = vi.fn()
    withLazyHydration(client, API, fetchImpl as unknown as typeof fetch)

    await client.threads.getState(THREAD, "checkpoint-1")
    expect(original).toHaveBeenCalledTimes(1)

    window.history.replaceState(null, "", "/agents?lazy=0")
    await client.threads.getState(THREAD)
    expect(original).toHaveBeenCalledTimes(2)
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it("surfaces the response status so the SDK can tell a missing thread apart", async () => {
    const { client } = fakeClient()
    withLazyHydration(client, API, async () =>
      jsonResponse({ detail: "nope" }, 404)
    )
    await expect(client.threads.getState(THREAD)).rejects.toMatchObject({
      status: 404,
    })
  })
})

describe("lazyMarker", () => {
  it("reads the skeleton marker off a trimmed tool message", () => {
    const trimmed = new ToolMessage({
      tool_call_id: "c",
      content: "preview",
      additional_kwargs: { [LAZY_MARKER_KEY]: { truncated: true, size: 9000 } },
    })
    expect(lazyMarker(trimmed)).toEqual({ truncated: true, size: 9000 })
    expect(
      lazyMarker(new ToolMessage({ tool_call_id: "c", content: "x" }))
    ).toBeNull()
  })
})
