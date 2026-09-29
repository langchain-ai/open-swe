/** @vitest-environment jsdom */

import { Client, ProtocolSseTransportAdapter } from "@langchain/langgraph-sdk"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import type { ReactNode } from "react"
import type { AgentStream } from "@/features/agents/lib/stream/connection"
import type { AgentThread } from "@/features/agents/lib/types"
import { agentThreadKeys } from "@/features/agents/lib/queries"
import { createAutoSelectionIntent } from "@/features/agents/lib/autoSelectionIntent"
import { createRunAcceptanceTracker } from "@/features/agents/lib/stream/runAcceptance"
import { useSubmitAgentMessage } from "@/features/agents/lib/provider/useSubmitAgentMessage"
import { useAgentStreamSource } from "./useAgentStreamSource"

const fetcher = vi.fn<typeof fetch>()
let acceptance: ReturnType<typeof createRunAcceptanceTracker>
const stream = {
  messages: [],
  toolCalls: [],
  error: undefined as unknown,
  submit: vi.fn<AgentStream["submit"]>(),
}

vi.mock("@/features/agents/lib/stream/useAgentThreadStream", () => ({
  useAgentThreadStream: () => ({
    stream,
    connection: { status: "connected" },
    trackRunAcceptance: acceptance.track,
  }),
}))
vi.mock("@langchain/react", () => ({
  useSubmissionQueue: () => ({ entries: [], cancel: vi.fn() }),
}))
vi.mock("./useCancelRun", () => ({ useCancelRun: () => vi.fn() }))
vi.mock("@/lib/session", () => ({ useSession: () => ({ data: {} }) }))
vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))
vi.mock("./ThreadSourceProvider", () => ({
  useThreadSource: () => useAgentStreamSource("thread-1"),
}))

beforeEach(() => {
  fetcher.mockReset()
  stream.submit.mockReset()
  stream.error = undefined
  acceptance = createRunAcceptanceTracker(fetcher)
})
afterEach(cleanup)

it.each([false, true])(
  "restores Auto only for rejected creation (queued=%s)",
  async (enqueue) => {
    const error = new Error("Run failed")
    const client = new QueryClient()
    client.setQueryData(agentThreadKeys.detail("thread-1"), {
      id: "thread-1",
      status: "idle",
      messages: [],
    } satisfies Pick<AgentThread, "id" | "status" | "messages">)
    const { result, rerender } = renderHook(
      () => ({
        source: useAgentStreamSource("thread-1"),
        send: useSubmitAgentMessage("thread-1"),
      }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      }
    )
    const intent = createAutoSelectionIntent()
    const transport = new ProtocolSseTransportAdapter({
      apiUrl: "http://localhost",
      threadId: "thread-1",
      fetch: acceptance.fetch,
    })
    let finish!: () => void
    stream.submit.mockImplementation(async (input, options) => {
      expect(options?.multitaskStrategy).toBe(enqueue ? "enqueue" : undefined)
      try {
        if (enqueue) {
          const queuedClient = new Client({
            apiUrl: "http://localhost",
            apiKey: null,
            callerOptions: { fetch: acceptance.fetch, maxRetries: 0 },
          })
          await queuedClient.runs.create("thread-1", "agent", {
            input,
            multitaskStrategy: "enqueue",
          })
        } else {
          await transport.send({
            id: 1,
            method: "run.start",
            params: {
              assistant_id: "agent",
              input: input ?? null,
            },
          })
        }
        await new Promise<void>((resolve) => {
          finish = resolve
        })
      } catch (failure) {
        stream.error = failure
        options?.onError?.(failure)
        return
      }
      stream.error = error
      options?.onError?.(error)
    })

    for (const accepted of [false, true]) {
      intent.select(true)
      const id = crypto.randomUUID()
      expect(intent.claim(id, true)).toBe(true)
      const restore = vi.fn(() => intent.restore(id))
      if (accepted)
        fetcher.mockResolvedValueOnce(
          Response.json(
            enqueue
              ? { run_id: "accepted-run" }
              : {
                  id: 1,
                  type: "success",
                  result: { run_id: "accepted-run" },
                }
          )
        )
      else fetcher.mockRejectedValueOnce(error)
      await act(async () => {
        await result.current.send.mutateAsync({
          content: "Continue",
          client_message_id: id,
          enqueue,
          model_selection_changed: true,
          onStartError: restore,
        })
      })
      await waitFor(() =>
        expect(accepted ? typeof finish : restore.mock.calls.length).toBe(
          accepted ? "function" : 1
        )
      )
      if (accepted)
        await act(async () => {
          finish()
        })
      rerender()
      expect(result.current.source.error).toBe(error)
      expect(restore).toHaveBeenCalledTimes(accepted ? 0 : 1)
      expect(intent.claim("next", true)).toBe(!accepted)
      const pending = client
        .getQueryData<AgentThread>(agentThreadKeys.detail("thread-1"))
        ?.pendingMessages?.find((message) => message.id === id)
      expect(pending?.status).toBe(accepted ? "sending" : "failed")
    }
  }
)

it("waits for command acceptance when execution fails before the command response", async () => {
  const error = new Error("Fast execution failure")
  let respond!: (response: Response) => void
  fetcher.mockReturnValueOnce(
    new Promise<Response>((resolve) => {
      respond = resolve
    })
  )
  const transport = new ProtocolSseTransportAdapter({
    apiUrl: "http://localhost",
    threadId: "thread-1",
    fetch: acceptance.fetch,
  })
  stream.submit.mockImplementation(async (input, options) => {
    void transport.send({
      id: 1,
      method: "run.start",
      params: {
        assistant_id: "agent",
        input: input ?? null,
      },
    })
    options?.onError?.(error)
  })
  const { result } = renderHook(() => useAgentStreamSource("thread-1"))
  const submission = result.current.startRun({
    message: { id: "message-1", text: "Continue" },
    configurable: {},
  })
  respond(
    Response.json({ id: 1, type: "success", result: { run_id: "run-1" } })
  )
  await expect(submission).resolves.toBeUndefined()
})
