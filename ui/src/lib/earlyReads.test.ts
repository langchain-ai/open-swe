/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { createElement } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  threadRunningRunUrl,
  threadStateUrl,
} from "@/features/agents/lib/apiWarmup"
import { useAgentThreadStream } from "@/features/agents/lib/stream/useAgentThreadStream"
import { subscribeRequestTimings } from "@/lib/perf/fetchTiming"
import { EarlyReads, earlyReads } from "./earlyReads"
import type { ReactNode } from "react"

function stubAgentServer(status = 200) {
  const network = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const path = new URL(String(input), location.href).pathname
    const body = path.endsWith("/runs")
      ? []
      : { values: { messages: [] }, next: [], tasks: [], metadata: {} }
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      })
    )
  })
  vi.stubGlobal("fetch", network)
  return network
}

function stateReads(network: ReturnType<typeof stubAgentServer>) {
  return network.mock.calls.filter(([input]) =>
    new URL(String(input), location.href).pathname.endsWith("/state")
  )
}

/** Mounts a thread page's stream and waits until it has loaded the thread. */
async function loadedThreadStream(
  threadId: string,
  network: ReturnType<typeof stubAgentServer>
) {
  const queryClient = new QueryClient()
  const hook = renderHook(() => useAgentThreadStream(threadId), {
    wrapper: ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children),
  })
  await waitFor(() =>
    expect(
      network.mock.calls.some(([input]) =>
        String(input).includes("status=pending")
      )
    ).toBe(true)
  )
  return hook
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("EarlyReads", () => {
  it("hands a state read started on a press to the thread page's new stream", async () => {
    const threadId = "1dd69115-f4b9-507f-b4d5-9f355f9f5ba0"
    const network = stubAgentServer()
    const timed: Array<string> = []
    const unsubscribe = subscribeRequestTimings((timing) =>
      timed.push(timing.kind)
    )
    earlyReads.start(threadStateUrl(threadId))

    await loadedThreadStream(threadId, network)
    unsubscribe()

    expect(stateReads(network)).toHaveLength(1)
    expect(timed, "the open still times its state read").toContain(
      "thread_state"
    )
  })

  it("hands a running-run lookup started on a press to the thread page's new stream", async () => {
    const threadId = "5dd69115-f4b9-507f-b4d5-9f355f9f5ba0"
    const network = stubAgentServer()
    earlyReads.start(threadStateUrl(threadId))
    earlyReads.start(threadRunningRunUrl(threadId))

    await loadedThreadStream(threadId, network)

    const runningLookups = network.mock.calls.filter(([input]) =>
      String(input).includes("status=running")
    )
    expect(runningLookups).toHaveLength(2)
    expect(stateReads(network)).toHaveLength(1)
  })

  it("never hands one to a stream reading its thread again", async () => {
    const threadId = "2dd69115-f4b9-507f-b4d5-9f355f9f5ba0"
    const network = stubAgentServer()
    const { result } = await loadedThreadStream(threadId, network)
    earlyReads.start(threadStateUrl(threadId))

    await act(() => result.current.stream.hydrate())

    expect(
      stateReads(network),
      "the stream's first read, the early read, the stream's second read"
    ).toHaveLength(3)
  })

  it("leaves a read unused once it is too old", async () => {
    const url = threadStateUrl("3dd69115-f4b9-507f-b4d5-9f355f9f5ba0")
    const network = stubAgentServer()
    const now = vi.spyOn(performance, "now").mockReturnValue(1_000)
    earlyReads.start(url)
    now.mockReturnValue(1_000 + EarlyReads.FRESH_MS)

    await earlyReads.newReader(network)(url)

    expect(stateReads(network)).toHaveLength(2)
  })

  it.each([
    { name: "an error status", fail: () => stubAgentServer(500) },
    {
      name: "no response",
      fail: () => {
        const answer = stubAgentServer()
        answer.mockImplementationOnce(() =>
          Promise.reject(new TypeError("Failed to fetch"))
        )
        return answer
      },
    },
  ])("leaves a read unused once it has failed ($name)", async ({ fail }) => {
    const url = threadStateUrl("4dd69115-f4b9-507f-b4d5-9f355f9f5ba0")
    const network = fail()
    earlyReads.start(url)
    await new Promise((resolve) => setTimeout(resolve))

    await earlyReads.newReader(network)(url)

    expect(stateReads(network)).toHaveLength(2)
  })
})
