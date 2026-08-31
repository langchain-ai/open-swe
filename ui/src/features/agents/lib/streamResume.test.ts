/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  forgetStreamEvents,
  recallStreamEvent,
  rememberStreamEvent,
  withStreamResume,
} from "./streamResume"
import type { Client } from "@langchain/langgraph-sdk"

interface JoinCall {
  runId: string
  options: { lastEventId?: string } | undefined
}

function fakeClient(events: Array<{ id?: string }>) {
  const joinCalls: Array<JoinCall> = []
  const runs = {
    // eslint-disable-next-line @typescript-eslint/require-await
    joinStream: async function* (
      _threadId: string | undefined | null,
      runId: string,
      options?: { lastEventId?: string }
    ) {
      joinCalls.push({ runId, options })
      yield* events
    },
    stream: async function* (
      _threadId: string | null,
      _assistantId: string,
      payload?: Record<string, unknown>
    ) {
      const onRunCreated = payload?.["onRunCreated"] as
        | ((p: { run_id: string; thread_id: string }) => void)
        | undefined
      onRunCreated?.({ run_id: "run-1", thread_id: "thread-1" })
      yield* events
      await Promise.resolve()
    },
  }
  return { client: { runs } as unknown as Client, joinCalls }
}

async function drain(generator: AsyncGenerator<unknown>) {
  for await (const _ of generator) {
    // consume
  }
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe("withStreamResume", () => {
  it("resumes a rejoin from the last event the run delivered", async () => {
    const { client, joinCalls } = fakeClient([{ id: "1" }, { id: "2" }])
    withStreamResume(client)

    await drain(client.runs.joinStream("thread-1", "run-1"))
    await drain(client.runs.joinStream("thread-1", "run-1"))

    expect(joinCalls[0]?.options?.lastEventId).toBeUndefined()
    expect(joinCalls[1]?.options?.lastEventId).toBe("2")
  })

  it("records ids from the initial run stream too", async () => {
    const { client } = fakeClient([{ id: "7" }])
    withStreamResume(client)

    await drain(
      client.runs.stream("thread-1", "agent") as AsyncGenerator<unknown>
    )

    expect(recallStreamEvent("thread-1", "run-1")).toBe("7")
  })

  it("treats the SDK's own -1 default as no resume point", async () => {
    const { client, joinCalls } = fakeClient([{ id: "3" }])
    withStreamResume(client)
    rememberStreamEvent("thread-1", "run-1", "3")

    await drain(
      client.runs.joinStream("thread-1", "run-1", { lastEventId: "-1" })
    )

    expect(joinCalls[0]?.options?.lastEventId).toBe("3")
  })

  it("keeps an explicit caller-supplied resume point", async () => {
    const { client, joinCalls } = fakeClient([{ id: "9" }])
    withStreamResume(client)
    rememberStreamEvent("thread-1", "run-1", "3")

    await drain(
      client.runs.joinStream("thread-1", "run-1", { lastEventId: "5" })
    )

    expect(joinCalls[0]?.options?.lastEventId).toBe("5")
  })

  it("does not resume across different runs", async () => {
    const { client, joinCalls } = fakeClient([{ id: "4" }])
    withStreamResume(client)

    await drain(client.runs.joinStream("thread-1", "run-1"))
    await drain(client.runs.joinStream("thread-1", "run-2"))

    expect(joinCalls[1]?.options?.lastEventId).toBeUndefined()
  })

  it("wraps a client only once", () => {
    const { client } = fakeClient([])
    const first = client.runs.joinStream
    withStreamResume(client)
    const wrapped = client.runs.joinStream
    withStreamResume(client)

    expect(wrapped).not.toBe(first)
    expect(client.runs.joinStream).toBe(wrapped)
  })

  it("leaves a client without a runs namespace alone", () => {
    const stub = {} as Client
    expect(withStreamResume(stub)).toBe(stub)
  })

  it("forgets a thread's resume points", async () => {
    const { client } = fakeClient([{ id: "6" }])
    withStreamResume(client)

    await drain(client.runs.joinStream("thread-1", "run-1"))
    expect(recallStreamEvent("thread-1", "run-1")).toBe("6")

    forgetStreamEvents("thread-1")
    expect(recallStreamEvent("thread-1", "run-1")).toBeUndefined()
  })
})
