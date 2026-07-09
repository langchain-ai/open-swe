import { describe, expect, it } from "vitest"

import { todosFromState } from "./todos"

describe("todosFromState", () => {
  it("returns validated todo state", () => {
    expect(
      todosFromState([
        { content: "Inspect the UI", status: "completed" },
        { content: "Render todos", status: "in_progress" },
      ])
    ).toEqual([
      { content: "Inspect the UI", status: "completed" },
      { content: "Render todos", status: "in_progress" },
    ])
  })

  it("rejects malformed todo state", () => {
    expect(todosFromState([{ content: "Broken", status: "unknown" }])).toEqual(
      []
    )
    expect(todosFromState({ todos: [] })).toEqual([])
  })
})
