/** @vitest-environment jsdom */

import { cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { AgentsApiError } from "@/features/agents/lib/api"
import type { AgentStream } from "@/features/agents/lib/stream/connection"
import type { RunAcceptance } from "@/features/agents/lib/stream/runAcceptance"
import { useAgentStreamSource } from "./useAgentStreamSource"

const accepted = vi.fn<() => Promise<RunAcceptance>>()
const stream = {
  messages: [],
  toolCalls: [],
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

beforeEach(() => {
  accepted.mockReset()
  stream.submit.mockReset()
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
    stream.submit.mockImplementation(async (_, options) => {
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
