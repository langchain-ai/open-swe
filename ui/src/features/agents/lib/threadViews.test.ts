import { describe, expect, it } from "vitest"

import {
  groupThreadsForView,
  moveColumn,
  moveColumnBefore,
  parseColumnOrder,
  reconcileColumnOrder,
} from "./threadViews"
import type { AgentThread } from "./types"

function thread(overrides: Partial<AgentThread> = {}): AgentThread {
  return {
    id: crypto.randomUUID(),
    title: "Thread",
    repo: "open-swe",
    repoFullName: "langchain-ai/open-swe",
    branch: "main",
    environment: "cloud",
    model: "default",
    source: "dashboard",
    status: "idle",
    viewed: true,
    createdAt: 1,
    updatedAt: 1,
    messages: [],
    ...overrides,
  }
}

describe("groupThreadsForView", () => {
  it("defaults every focus state into exactly one ordered group", () => {
    const threads = [
      thread({ id: "done", resolved: true, status: "running" }),
      thread({ id: "running", status: "running" }),
      thread({ id: "error", status: "error" }),
      thread({ id: "unread", status: "finished", viewed: false }),
      thread({ id: "plan", status: "finished", planStatus: "ready" }),
      thread({ id: "ready", status: "finished" }),
    ]

    const groups = groupThreadsForView(threads, "focus")

    expect(groups.map((group) => group.key)).toEqual([
      "attention",
      "progress",
      "ready",
      "done",
    ])
    expect(
      groups.flatMap((group) => group.threads.map((item) => item.id)).sort()
    ).toEqual(threads.map((item) => item.id).sort())
  })

  it("uses the canonical status order without treating resolved as a status", () => {
    const groups = groupThreadsForView(
      [
        thread({ status: "idle", resolved: true }),
        thread({ status: "error" }),
        thread({ status: "interrupted" }),
        thread({ status: "finished" }),
        thread({ status: "running" }),
      ],
      "status"
    )

    expect(groups.map((group) => group.key)).toEqual([
      "running",
      "finished",
      "interrupted",
      "error",
      "idle",
    ])
    expect(groups.at(-1)?.threads[0]?.resolved).toBe(true)
  })

  it("groups dynamic repositories alphabetically with an empty fallback", () => {
    const groups = groupThreadsForView(
      [
        thread({ repoFullName: "z/repo" }),
        thread({ repoFullName: "a/repo" }),
        thread({ repoFullName: "" }),
      ],
      "repo"
    )

    expect(groups.map((group) => group.label)).toEqual([
      "a/repo",
      "No repository",
      "z/repo",
    ])
  })
})

describe("column ordering", () => {
  it("parses, reconciles, and appends new dynamic groups", () => {
    expect(parseColumnOrder("ready|attention|stale|ready")).toEqual([
      "ready",
      "attention",
      "stale",
      "ready",
    ])
    expect(
      reconcileColumnOrder(
        ["attention", "progress", "ready", "done"],
        parseColumnOrder("ready|attention|stale|ready")
      )
    ).toEqual(["ready", "attention", "progress", "done"])
  })

  it("moves columns with buttons or drag targets", () => {
    const order = ["attention", "progress", "ready", "done"]
    expect(moveColumn(order, "ready", -1)).toEqual([
      "attention",
      "ready",
      "progress",
      "done",
    ])
    expect(moveColumnBefore(order, "done", "progress")).toEqual([
      "attention",
      "done",
      "progress",
      "ready",
    ])
    expect(moveColumnBefore(order, "attention", "ready")).toEqual([
      "progress",
      "attention",
      "ready",
      "done",
    ])
    expect(moveColumnBefore(order, "attention", "progress")).toEqual(order)
  })
})
