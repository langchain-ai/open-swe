import { beforeEach, describe, expect, it } from "vitest"

import {
  isQueuedMessageDue,
  latestCompletedToolCallId,
  useQueuedMessageStore,
} from "./queuedMessageStore"
import type { QueuedComposerMessage } from "./queuedMessageStore"
import type { Message } from "@/features/agents/lib/types"

function makeMessage(text: string): Omit<QueuedComposerMessage, "id"> {
  return {
    text,
    images: [],
    modelId: null,
    effort: null,
    planMode: false,
    queuedAfterToolCallId: null,
    createdAt: 1,
  }
}

function texts(threadId: string): Array<string> | undefined {
  return useQueuedMessageStore
    .getState()
    .queuesByThreadId[threadId]?.map((message) => message.text)
}

describe("queuedMessageStore", () => {
  beforeEach(() => {
    useQueuedMessageStore.setState({ queuesByThreadId: {}, drainGeneration: 0 })
  })

  it("keeps messages in submission order per thread", () => {
    const { enqueue } = useQueuedMessageStore.getState()
    enqueue("thread-a", makeMessage("first"))
    enqueue("thread-a", makeMessage("second"))
    enqueue("thread-b", makeMessage("other"))

    expect(texts("thread-a")).toEqual(["first", "second"])
    expect(texts("thread-b")).toEqual(["other"])
  })

  it("take hands the message to exactly one caller", () => {
    const { enqueue, take } = useQueuedMessageStore.getState()
    const entry = enqueue("thread-a", makeMessage("first"))

    expect(take("thread-a", entry.id, null)?.text).toBe("first")
    expect(take("thread-a", entry.id, null)).toBeNull()
    expect(texts("thread-a")).toBeUndefined()
  })

  it("take re-anchors the remaining messages to the current tool boundary", () => {
    const { enqueue, take } = useQueuedMessageStore.getState()
    const first = enqueue("thread-a", makeMessage("first"))
    enqueue("thread-a", makeMessage("second"))

    take("thread-a", first.id, "tool-2")

    const [second] =
      useQueuedMessageStore.getState().queuesByThreadId["thread-a"] ?? []
    expect(second?.queuedAfterToolCallId).toBe("tool-2")
    expect(
      isQueuedMessageDue({
        message: second!,
        phase: "running",
        latestToolCallId: "tool-2",
      })
    ).toBe(false)
  })

  it("remove keeps the other messages' anchors", () => {
    const { enqueue, remove } = useQueuedMessageStore.getState()
    const first = enqueue("thread-a", {
      ...makeMessage("first"),
      queuedAfterToolCallId: "t1",
    })
    const second = enqueue("thread-a", makeMessage("second"))

    expect(remove("thread-a", second.id)?.text).toBe("second")
    expect(remove("thread-a", second.id)).toBeNull()
    expect(
      useQueuedMessageStore.getState().queuesByThreadId["thread-a"]
    ).toEqual([first])
  })

  it("holdAtFront returns a failed message to the head, held", () => {
    const { enqueue, take, holdAtFront } = useQueuedMessageStore.getState()
    const first = enqueue("thread-a", makeMessage("first"))
    enqueue("thread-a", makeMessage("second"))
    const taken = take("thread-a", first.id, "t1")!

    holdAtFront("thread-a", taken)

    const queue =
      useQueuedMessageStore.getState().queuesByThreadId["thread-a"] ?? []
    expect(queue.map((message) => message.text)).toEqual(["first", "second"])
    expect(queue[0]?.holdUntilUserAction).toBe(true)
    expect(
      isQueuedMessageDue({
        message: queue[0]!,
        phase: "ready",
        latestToolCallId: null,
      })
    ).toBe(false)
  })

  it("drain empties one thread's queue in order", () => {
    const { enqueue, drain } = useQueuedMessageStore.getState()
    enqueue("thread-a", makeMessage("first"))
    enqueue("thread-a", makeMessage("second"))
    enqueue("thread-b", makeMessage("other"))

    expect(drain("thread-a").map((message) => message.text)).toEqual([
      "first",
      "second",
    ])
    expect(useQueuedMessageStore.getState().drainGeneration).toBe(1)
    expect(drain("thread-a")).toEqual([])
    expect(useQueuedMessageStore.getState().drainGeneration).toBe(1)
    expect(texts("thread-b")).toHaveLength(1)
  })
})

describe("queued message dispatch timing", () => {
  const messages = [
    {
      id: "m1",
      author: "agent",
      timestamp: "2026-01-01T00:00:00Z",
      chunks: [
        {
          kind: "tool-execution",
          toolCallId: "t1",
          title: "Read",
          toolKind: "read",
          status: "completed",
        },
        {
          kind: "tool-execution",
          toolCallId: "t2",
          title: "Edit",
          toolKind: "edit",
          status: "in_progress",
        },
      ],
    },
  ] as unknown as Array<Message>

  it("finds the newest settled tool call and ignores the ones still running", () => {
    expect(latestCompletedToolCallId(messages)).toBe("t1")
    expect(latestCompletedToolCallId([])).toBeNull()
  })

  it("waits mid-run until a tool call finishes after the message was queued", () => {
    const message = { queuedAfterToolCallId: "t1" }
    expect(
      isQueuedMessageDue({ message, phase: "running", latestToolCallId: "t1" })
    ).toBe(false)
    expect(
      isQueuedMessageDue({ message, phase: "running", latestToolCallId: "t2" })
    ).toBe(true)
  })

  it("never auto-sends a message held for user action", () => {
    const message = { queuedAfterToolCallId: null, holdUntilUserAction: true }
    expect(
      isQueuedMessageDue({ message, phase: "ready", latestToolCallId: "t2" })
    ).toBe(false)
  })

  it("is due as soon as the run is over, but not while a send is connecting", () => {
    const message = { queuedAfterToolCallId: "t1" }
    expect(
      isQueuedMessageDue({ message, phase: "ready", latestToolCallId: "t1" })
    ).toBe(true)
    expect(
      isQueuedMessageDue({
        message,
        phase: "connecting",
        latestToolCallId: "t2",
      })
    ).toBe(false)
  })
})
