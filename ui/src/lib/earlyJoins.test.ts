/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, renderHook, waitFor } from "@testing-library/react"
import { createElement } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { threadRunningJoinUrl } from "@/features/agents/lib/apiWarmup"
import { useAgentThreadStream } from "@/features/agents/lib/stream/useAgentThreadStream"
import { earlyJoins } from "./earlyJoins"
import type { ReactNode } from "react"

const RUN = "01a1f3b3-fa88-7563-b7f6-5907eb5a6983"
const HEAD = "1f1e4126-cfb3-694b-816c-92a57a805664"

/** An open run stream: its first event, then nothing until it is aborted. */
function openStream(
  signal: AbortSignal | null | undefined,
  headers: HeadersInit
) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(
          `event: metadata\ndata: {"run_id": "${RUN}", "attempt": 1}\n\n`
        )
      )
      signal?.addEventListener("abort", () => controller.error(signal.reason))
    },
  })
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream", ...headers },
  })
}

/** The agent server behind the dashboard, with a run going on the thread. */
function stubRunningThread(
  threadId: string,
  { earlyJoin, v3 = false }: { earlyJoin: string; v3?: boolean }
) {
  const joined = { early: [] as AbortSignal[], page: [] as string[] }
  const network = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), location.href)
    const json = (body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          headers: { "content-type": "application/json" },
        })
      )
    if (url.pathname.endsWith("/runs/running/stream")) {
      if (init?.signal) joined.early.push(init.signal)
      return Promise.resolve(
        openStream(init?.signal, {
          "content-location": `/threads/${threadId}/runs/${RUN}`,
          "x-robin-join": `/threads/${threadId}/runs/${RUN}/stream?${earlyJoin}`,
        })
      )
    }
    if (url.pathname.endsWith(`/runs/${RUN}/stream`)) {
      joined.page.push(url.search)
      return Promise.resolve(
        openStream(init?.signal, {
          "content-location": `/threads/${threadId}/runs/${RUN}`,
        })
      )
    }
    if (url.pathname.endsWith("/info"))
      return json({
        flags: { stream_namespaces: true, stream_lifecycle: true },
      })
    if (url.pathname.endsWith("/runs"))
      return json(
        url.searchParams.get("status") === "running"
          ? [
              {
                run_id: RUN,
                kwargs: {
                  stream_mode: ["updates", "messages-tuple", "lifecycle"],
                  subgraphs: true,
                  ...(v3
                    ? {
                        config: {
                          configurable: { __event_streaming_v2: true },
                        },
                      }
                    : {}),
                },
              },
            ]
          : []
      )
    return json({
      values: { messages: [] },
      next: ["model"],
      tasks: [],
      metadata: { step: 3 },
      checkpoint: {
        thread_id: threadId,
        checkpoint_ns: "",
        checkpoint_id: HEAD,
      },
      parent_checkpoint: null,
      created_at: new Date().toISOString(),
    })
  })
  vi.stubGlobal("fetch", network)
  return joined
}

function mountThreadStream(threadId: string) {
  const queryClient = new QueryClient()
  return renderHook(() => useAgentThreadStream(threadId), {
    wrapper: ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children),
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("EarlyJoins", () => {
  it.each([
    {
      run: "streams the root graph alone",
      v3: false,
      earlyJoin: `cancel_on_disconnect=0&namespace=&checkpoint_id=${HEAD}`,
    },
    {
      run: "streams v3 frames",
      v3: true,
      earlyJoin: `cancel_on_disconnect=0&checkpoint_id=${HEAD}`,
    },
  ])(
    "hands the join a press started to the page that asks for it, when the run $run",
    async ({ v3, earlyJoin }) => {
      const threadId = crypto.randomUUID()
      const joined = stubRunningThread(threadId, { earlyJoin, v3 })
      earlyJoins.start(threadId, threadRunningJoinUrl(threadId))

      const hook = mountThreadStream(threadId)

      await waitFor(() =>
        expect(hook.result.current.stream.isLoading).toBe(true)
      )
      await waitFor(() => expect(joined.early).toHaveLength(1))
      await new Promise((resolve) => setTimeout(resolve, 50))
      expect(joined.page).toEqual([])
      hook.unmount()
      await waitFor(() => expect(joined.early[0]?.aborted).toBe(true))
    }
  )

  it("closes the early join and joins as before when the page asks for another", async () => {
    const threadId = crypto.randomUUID()
    const joined = stubRunningThread(threadId, {
      earlyJoin:
        "cancel_on_disconnect=0&namespace=&checkpoint_id=1f1e4126-0000-6000-8000-000000000000",
    })
    earlyJoins.start(threadId, threadRunningJoinUrl(threadId))

    const hook = mountThreadStream(threadId)

    await waitFor(() =>
      expect(joined.page).toEqual([
        `?cancel_on_disconnect=0&namespace=&checkpoint_id=${HEAD}`,
      ])
    )
    expect(joined.early[0]?.aborted).toBe(true)
    hook.unmount()
  })
})
