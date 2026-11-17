/** @vitest-environment jsdom */

import { cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { AgentsApiError } from "@/features/agents/lib/api"
import type { AgentStream } from "@/features/agents/lib/stream/connection"
import type { RunAcceptance } from "@/features/agents/lib/stream/runAcceptance"
import { startRun as postRunStart } from "@/features/agents/lib/transcript/api"
import { useAgentStreamSource } from "./useAgentStreamSource"

const accepted = vi.fn<() => Promise<RunAcceptance>>()
const stream = {
  messages: [],
  toolCalls: [],
  isLoading: false,
  submit: vi.fn<AgentStream["submit"]>(),
}

vi.mock("@/features/agents/lib/stream/useAgentThreadStream", () => ({
  useAgentThreadStream: () => ({
    stream,
    connection: { status: "connected" },
    trackRunAcceptance: () => accepted,
  }),
}))
vi.mock("@langchain/react", () => ({
  useSubmissionQueue: () => ({ entries: [], cancel: vi.fn() }),
}))
vi.mock("./useCancelRun", () => ({ useCancelRun: () => vi.fn() }))
vi.mock("@/lib/session", () => ({ useSession: () => ({ data: {} }) }))
vi.mock("@/features/agents/lib/transcript/api", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@/features/agents/lib/transcript/api")
  >()),
  startRun: vi.fn(),
}))

beforeEach(() => {
  accepted.mockReset()
  stream.submit.mockReset()
  stream.isLoading = false
  vi.mocked(postRunStart).mockReset()
})
afterEach(cleanup)

const error = new Error("Run failed")
const refusal = new AgentsApiError(410, "This thread is read-only.")

it.each<[string, RunAcceptance, PromiseSettledResult<void>]>([
  ["rejected", { accepted: false }, { status: "rejected", reason: error }],
  ["accepted", { accepted: true }, { status: "fulfilled", value: undefined }],
  [
    "refused",
    { accepted: false, refusal },
    { status: "rejected", reason: refusal },
  ],
])(
  "settles a failed submission by whether run creation was %s",
  async (_, acceptance, settled) => {
    let respond!: (value: RunAcceptance) => void
    accepted.mockReturnValueOnce(
      new Promise<RunAcceptance>((resolve) => {
        respond = resolve
      })
    )
    stream.submit.mockImplementation(async (_impl, options) => {
      options?.onError?.(error)
    })
    const { result } = renderHook(() => useAgentStreamSource("thread-1"))
    const submission = result.current.startRun({
      message: { id: "message-1", text: "Continue" },
      configurable: {},
    })
    respond(acceptance)
    expect(await Promise.allSettled([submission])).toEqual([settled])
  }
)

it("steers a plain send made during a run through /commands", async () => {
  stream.isLoading = true
  const onAccepted = vi.fn()
  const { result } = renderHook(() => useAgentStreamSource("thread-1"))

  await result.current.startRun({
    message: { id: "message-1", text: "Use the other branch" },
    configurable: { agent_effort: "high" },
    onAccepted,
  })

  expect(vi.mocked(postRunStart).mock.calls).toEqual([
    [
      "thread-1",
      expect.objectContaining({
        method: "run.start",
        params: expect.objectContaining({
          input: {
            messages: [
              expect.objectContaining({
                id: "message-1",
                content: [{ type: "text", text: "Use the other branch" }],
              }),
            ],
          },
          config: {
            configurable: { agent_effort: "high", thread_id: "thread-1" },
          },
        }),
      }),
    ],
  ])
  expect(stream.submit).not.toHaveBeenCalled()
  expect(onAccepted).toHaveBeenCalledOnce()
})

it.each([
  { running: false, enqueue: false, strategy: undefined },
  { running: true, enqueue: true, strategy: "enqueue" },
])(
  "sends through useStream with strategy $strategy when a run going is $running and enqueue is $enqueue",
  async ({ running, enqueue, strategy }) => {
    stream.isLoading = running
    accepted.mockResolvedValue({ accepted: true })
    const { result } = renderHook(() => useAgentStreamSource("thread-1"))

    await result.current.startRun({
      message: { id: "message-1", text: "Use the other branch" },
      configurable: {},
      enqueue,
    })

    expect(postRunStart).not.toHaveBeenCalled()
    expect(stream.submit).toHaveBeenCalledOnce()
    expect(stream.submit.mock.calls[0]?.[1]?.multitaskStrategy).toBe(strategy)
  }
)
