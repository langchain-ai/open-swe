import { describe, expect, it } from "vitest"

import { applyThreadIndexEvent, replaceThread } from "./threadIndexReducer"
import type { InfiniteData } from "@tanstack/react-query"
import type { ThreadsPage } from "./api"
import type { AgentThread } from "./types"

function thread(
  id: string,
  createdAt: number,
  updatedAt = createdAt,
  title = id
): AgentThread {
  return {
    id,
    title,
    repo: "",
    repoFullName: "",
    branch: "",
    model: "",
    status: "idle",
    viewed: true,
    createdAt,
    updatedAt,
    messages: [],
  }
}

function list(
  pages: Array<Array<AgentThread>>,
  hasMore = false
): InfiniteData<ThreadsPage> {
  return {
    pages: pages.map((items, index) => ({
      items,
      limit: 2,
      offset: index * 2,
      hasMore: index < pages.length - 1 || hasMore,
    })),
    pageParams: pages.map((_, index) => ({ offset: index * 2 })),
  }
}

const ids = (data: InfiniteData<ThreadsPage>) =>
  data.pages.map((page) => page.items.map((item) => item.id))

describe("applyThreadIndexEvent", () => {
  it("inserts a newer thread at the head of page 0", () => {
    const data = list([[thread("b", 20), thread("a", 10)]])
    const next = applyThreadIndexEvent(
      data,
      { type: "upsert", thread: thread("c", 30) },
      "created_at"
    )
    expect(ids(next)).toEqual([["c", "b", "a"]])
  })

  it("inserts a thread into the loaded page it sorts into", () => {
    const data = list([[thread("d", 40), thread("c", 30)], [thread("a", 10)]])
    const next = applyThreadIndexEvent(
      data,
      { type: "upsert", thread: thread("b", 20) },
      "created_at"
    )
    expect(ids(next)).toEqual([
      ["d", "c"],
      ["b", "a"],
    ])
  })

  it("leaves a thread older than every loaded row to the next page", () => {
    const data = list([[thread("b", 20), thread("a", 10)]], true)
    const next = applyThreadIndexEvent(
      data,
      { type: "upsert", thread: thread("old", 5) },
      "created_at"
    )
    expect(next).toBe(data)
  })

  it("appends an older thread when no more pages exist", () => {
    const data = list([[thread("b", 20), thread("a", 10)]])
    const next = applyThreadIndexEvent(
      data,
      { type: "upsert", thread: thread("old", 5) },
      "created_at"
    )
    expect(ids(next)).toEqual([["b", "a", "old"]])
  })

  it("replaces a thread in a later page in place", () => {
    const data = list([[thread("d", 40), thread("c", 30)], [thread("a", 10)]])
    const next = applyThreadIndexEvent(
      data,
      { type: "upsert", thread: thread("a", 10, 99, "renamed") },
      "created_at"
    )
    expect(ids(next)).toEqual([["d", "c"], ["a"]])
    expect(next.pages[1]?.items[0]?.title).toBe("renamed")
    expect(next.pages[0]).toBe(data.pages[0])
  })

  it("moves an updated thread to the top when sorted by update time", () => {
    const data = list([[thread("d", 40), thread("c", 30)], [thread("a", 10)]])
    const next = applyThreadIndexEvent(
      data,
      { type: "upsert", thread: thread("a", 10, 99) },
      "updated_at"
    )
    expect(ids(next)).toEqual([["a", "d", "c"], []])
  })

  it("orders equal timestamps by id, as the server does", () => {
    const data = list([[thread("c", 10), thread("a", 10)]])
    const next = applyThreadIndexEvent(
      data,
      { type: "upsert", thread: thread("b", 10) },
      "created_at"
    )
    expect(ids(next)).toEqual([["c", "b", "a"]])
  })

  it("removes a thread from whichever page holds it", () => {
    const data = list([[thread("d", 40), thread("c", 30)], [thread("a", 10)]])
    const next = applyThreadIndexEvent(
      data,
      { type: "remove", threadId: "c" },
      "created_at"
    )
    expect(ids(next)).toEqual([["d"], ["a"]])
    expect(
      applyThreadIndexEvent(
        next,
        { type: "remove", threadId: "gone" },
        "created_at"
      )
    ).toBe(next)
  })
})

describe("replaceThread", () => {
  it("never adds a thread the list does not hold", () => {
    const data = list([[thread("a", 10)]])
    expect(replaceThread(data, thread("b", 20))).toBe(data)
  })
})
