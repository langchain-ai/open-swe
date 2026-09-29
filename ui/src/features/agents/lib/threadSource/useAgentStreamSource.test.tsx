/** @vitest-environment jsdom */

import { cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import type { AgentStream } from "@/features/agents/lib/stream/connection"
import { useAgentStreamSource } from "./useAgentStreamSource"

const accepted = vi.fn<() => Promise<boolean>>()
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

it.each([false, true])(
  "rejects a failed submission only if run creation was rejected (accepted=%s)",
  async (wasAccepted) => {
    const error = new Error("Run failed")
    let respond!: (value: boolean) => void
    accepted.mockReturnValueOnce(
      new Promise<boolean>((resolve) => {
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
    respond(wasAccepted)
    expect(await Promise.allSettled([submission])).toEqual([
      wasAccepted
        ? { status: "fulfilled", value: undefined }
        : { status: "rejected", reason: error },
    ])
  }
)
