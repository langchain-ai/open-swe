import { describe, expect, it } from "vitest"

import { buildInbox, inSplit } from "./inbox"
import type { AgentThread } from "./types"

function makeThread(id: string, updatedAt: number): AgentThread {
  return {
    id,
    title: id,
    repo: "repo",
    repoFullName: "acme/repo",
    branch: "main",
    model: "gpt-5",
    source: "dashboard",
    status: "finished",
    viewed: true,
    createdAt: updatedAt,
    updatedAt,
    messages: [],
  }
}

const NOW = 1_000_000

function inbox(threads: Array<AgentThread>, snoozedAt: number, until: number) {
  return buildInbox({
    threads,
    reviews: [],
    done: new Set(),
    snoozes: [{ key: "cloud:a", snoozed_at_ms: snoozedAt, until_ms: until }],
    now: NOW,
  })
}

describe("buildInbox snoozes", () => {
  it("moves a snoozed item to the snoozed split until its time", () => {
    const items = inbox([makeThread("a", NOW - 50)], NOW - 10, NOW + 10)
    expect(items.filter((item) => inSplit(item, "all"))).toEqual([])
    expect(items.filter((item) => inSplit(item, "snoozed"))).toMatchObject([
      { key: "cloud:a", snoozedUntil: NOW + 10 },
    ])
  })

  it("brings an item back early when it changes after being snoozed", () => {
    const [item] = inbox([makeThread("a", NOW - 5)], NOW - 10, NOW + 10)
    expect(item).toMatchObject({ backFromSnooze: "activity" })
    expect(item && inSplit(item, "all")).toBe(true)
  })

  it("sorts an item whose snooze ended above quieter newer items", () => {
    const items = inbox(
      [makeThread("a", NOW - 500), makeThread("b", NOW - 100)],
      NOW - 400,
      NOW - 50
    )
    expect(items.map((item) => [item.key, item.backFromSnooze])).toEqual([
      ["cloud:a", "time"],
      ["cloud:b", undefined],
    ])
  })
})
