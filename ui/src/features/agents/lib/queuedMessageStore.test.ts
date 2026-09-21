import { beforeEach, describe, expect, it } from "vitest"

import { isQueuedMessageDue, useQueuedMessageStore } from "./queuedMessageStore"
import type { QueuedComposerMessage } from "./queuedMessageStore"

function makeMessage(text: string): Omit<QueuedComposerMessage, "id"> {
  return {
    text,
    images: [],
    modelId: null,
    effort: null,
    planMode: false,
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

    expect(take("thread-a", entry.id)?.text).toBe("first")
    expect(take("thread-a", entry.id)).toBeNull()
    expect(texts("thread-a")).toBeUndefined()
  })

  it("remove leaves the other messages in place", () => {
    const { enqueue, remove } = useQueuedMessageStore.getState()
    const first = enqueue("thread-a", makeMessage("first"))
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
    const taken = take("thread-a", first.id)!

    holdAtFront("thread-a", taken)

    const queue =
      useQueuedMessageStore.getState().queuesByThreadId["thread-a"] ?? []
    expect(queue.map((message) => message.text)).toEqual(["first", "second"])
    expect(queue[0]?.holdUntilUserAction).toBe(true)
    expect(isQueuedMessageDue({ message: queue[0]!, phase: "ready" })).toBe(
      false
    )
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
  it("waits for the run to end, and for a send in flight to land", () => {
    const message = {}
    expect(isQueuedMessageDue({ message, phase: "running" })).toBe(false)
    expect(isQueuedMessageDue({ message, phase: "connecting" })).toBe(false)
    expect(isQueuedMessageDue({ message, phase: "ready" })).toBe(true)
  })

  it("never auto-sends a message held for user action", () => {
    const message = { holdUntilUserAction: true }
    expect(isQueuedMessageDue({ message, phase: "ready" })).toBe(false)
  })
})
