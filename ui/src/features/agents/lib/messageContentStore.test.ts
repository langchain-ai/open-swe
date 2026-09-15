import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  resetMessageContentStore,
  useMessageContentStore,
} from "./messageContentStore"

const getThreadMessage = vi.fn()
const getThreadState = vi.fn()

vi.mock("./api", () => ({
  agentsApi: {
    getThreadMessage: (...args: Array<unknown>) => getThreadMessage(...args),
    getThreadState: (...args: Array<unknown>) => getThreadState(...args),
  },
}))

beforeEach(resetMessageContentStore)
afterEach(() => vi.clearAllMocks())

describe("messageContentStore", () => {
  it("fetches a message once and caches its content", async () => {
    getThreadMessage.mockResolvedValue({ id: "t1", content: "big output" })
    const { ensure } = useMessageContentStore.getState()

    const [first, second] = await Promise.all([
      ensure("thread-1", "t1"),
      ensure("thread-1", "t1"),
    ])

    expect(first).toBe(true)
    expect(second).toBe(true)
    expect(getThreadMessage).toHaveBeenCalledTimes(1)
    expect(useMessageContentStore.getState().contents["thread-1"]).toEqual({
      t1: "big output",
    })
    await ensure("thread-1", "t1")
    expect(getThreadMessage).toHaveBeenCalledTimes(1)
  })

  it("reports a failed fetch and allows a retry", async () => {
    getThreadMessage.mockRejectedValueOnce(new Error("boom"))
    getThreadMessage.mockResolvedValueOnce({ id: "t1", content: "later" })
    const { ensure } = useMessageContentStore.getState()

    expect(await ensure("thread-1", "t1")).toBe(false)
    expect(await ensure("thread-1", "t1")).toBe(true)
  })

  it("prefetches every large content of a thread from the full state", async () => {
    getThreadState.mockResolvedValue({
      values: {
        messages: [
          { id: "h1", type: "human", content: "hello" },
          {
            id: "h2",
            type: "human",
            content: [{ type: "image", base64: "AA" }],
          },
          { id: "t1", type: "tool", content: "output" },
          { type: "tool", content: "no id" },
        ],
      },
    })
    const { prefetch } = useMessageContentStore.getState()

    await prefetch("thread-1")
    await prefetch("thread-1")

    expect(getThreadState).toHaveBeenCalledTimes(1)
    expect(getThreadState).toHaveBeenCalledWith("thread-1", "full")
    expect(useMessageContentStore.getState().contents["thread-1"]).toEqual({
      h2: [{ type: "image", base64: "AA" }],
      t1: "output",
    })
  })
})
