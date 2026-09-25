import { describe, expect, it } from "vitest"

import {
  mergeThreadSummary,
  replaceThreadInPages,
} from "./threadChangesReducer"
import type { InfiniteData } from "@tanstack/react-query"
import type { ThreadsPage } from "./api"
import type { AgentThread, Message } from "./types"

function thread(id: string, fields: Partial<AgentThread> = {}): AgentThread {
  return { id, status: "running", messages: [], ...fields } as AgentThread
}

function pages(...items: Array<Array<AgentThread>>): InfiniteData<ThreadsPage> {
  return {
    pages: items.map((page, index) => ({
      items: page,
      limit: 2,
      offset: index * 2,
      hasMore: index < items.length - 1,
    })),
    pageParams: items.map((_, index) => index * 2),
  }
}

describe("replaceThreadInPages", () => {
  it("swaps the thread in place on whichever page holds it", () => {
    const data = pages([thread("a"), thread("b")], [thread("c"), thread("d")])

    const replaced = replaceThreadInPages(
      data,
      thread("c", { status: "finished" })
    )

    expect(replaced.found).toBe(true)
    expect(
      replaced.value.pages.map((page) =>
        page.items.map((item) => `${item.id}:${item.status}`)
      )
    ).toEqual([
      ["a:running", "b:running"],
      ["c:finished", "d:running"],
    ])
    expect(replaced.value.pages[0]).toBe(data.pages[0])
  })

  it("leaves the pages untouched for a thread they do not hold", () => {
    const data = pages([thread("a")])

    const replaced = replaceThreadInPages(data, thread("new"))

    expect(replaced).toEqual({ value: data, found: false })
    expect(replaced.value).toBe(data)
  })
})

describe("mergeThreadSummary", () => {
  it("keeps the conversation only the detail knows", () => {
    const messages = [{ id: "m-1" }] as Array<Message>
    const detail = thread("a", { messages, title: "Old" })

    const merged = mergeThreadSummary(
      detail,
      thread("a", { status: "finished", title: "New", messages: [] })
    )

    expect(merged).toMatchObject({ status: "finished", title: "New" })
    expect(merged.messages).toBe(messages)
  })
})
