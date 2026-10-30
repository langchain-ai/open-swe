import { describe, expect, it } from "vitest"

import {
  DEFAULT_SIDEBAR_FILTERS,
  filterThreads,
  hasActiveFilters,
} from "./sidebarFilter"
import type { SidebarFilters } from "./sidebarFilter"
import type { AgentThread } from "./types"
import { cloudSidebarThread } from "./sidebarThreads"
import type { SidebarThreadItem } from "./sidebarThreads"

function makeThread(overrides: Partial<AgentThread> = {}): AgentThread {
  return {
    id: Math.random().toString(36).slice(2),
    title: "Thread",
    repo: "repo",
    repoFullName: "acme/repo",
    branch: "main",
    model: "gpt-5",
    source: "dashboard",
    status: "idle",
    viewed: true,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    messages: [],
    ...overrides,
  }
}

function filters(overrides: Partial<SidebarFilters> = {}): SidebarFilters {
  return { ...DEFAULT_SIDEBAR_FILTERS, ...overrides }
}

describe("filterThreads", () => {
  it("hides Slack questions by default without hiding edits or PRs", () => {
    const question = makeThread({ source: "slack", hasEdits: false })
    const edited = makeThread({ source: "slack", hasEdits: true })
    const pr = makeThread({
      source: "slack",
      hasEdits: false,
      pr: {
        number: 1,
        title: "Fix",
        state: "closed",
        headRef: "fix",
        baseRef: "main",
        url: "https://github.com/acme/repo/pull/1",
      },
    })
    const unknown = makeThread({ source: "slack", hasEdits: null })
    const dashboard = makeThread({ hasEdits: false })
    const threads = [question, edited, pr, unknown, dashboard]
    const lists: Array<Array<AgentThread | SidebarThreadItem>> = [
      threads,
      threads.map(cloudSidebarThread),
    ]
    for (const items of lists) {
      expect(filterThreads(items, filters()).map((item) => item.id)).toEqual(
        [edited, pr, unknown, dashboard].map((thread) => thread.id)
      )
      expect(
        filterThreads(items, filters({ includeSlackQuestions: true }))
      ).toEqual(items)
      expect(filterThreads(items, filters(), question.id)).toEqual(items)
    }
  })

  it("returns ordinary threads with default filters", () => {
    const threads = [
      makeThread(),
      makeThread({ source: "schedule", threadCategory: "automation" }),
    ]
    expect(filterThreads(threads, DEFAULT_SIDEBAR_FILTERS)).toHaveLength(1)
  })

  it("keeps the actively viewed thread visible despite source and automation filters", () => {
    const active = makeThread({
      id: "active-thread",
      source: "slack",
      threadCategory: "automation",
    })
    const hidden = makeThread({ source: "slack" })
    const visible = makeThread({ source: "github" })

    expect(
      filterThreads(
        [active, hidden, visible],
        filters({ sources: ["github"] }),
        active.id
      )
    ).toEqual([active, visible])
  })

  it("hides archived threads even when actively viewed unless Show archived is enabled", () => {
    const archived = makeThread({ id: "archived", resolved: true })
    const ordinary = makeThread()
    const threads = [archived, ordinary]

    expect(filterThreads(threads, filters(), archived.id)).toEqual([ordinary])
    expect(
      filterThreads(threads, filters({ includeResolved: true }), archived.id)
    ).toEqual(threads)
  })

  it("includes automations when requested", () => {
    const ordinary = makeThread()
    const automation = makeThread({
      source: "schedule",
      threadCategory: "automation",
    })
    expect(
      filterThreads(
        [ordinary, automation],
        filters({ includeAutomations: true })
      )
    ).toEqual([ordinary, automation])
  })

  it("includes automations when Schedule is the selected source", () => {
    const ordinary = makeThread()
    const automation = makeThread({
      source: "schedule",
      threadCategory: "automation",
    })
    expect(
      filterThreads([ordinary, automation], filters({ sources: ["schedule"] }))
    ).toEqual([automation])
  })

  it("filters by source, defaulting missing source to dashboard", () => {
    const gh = makeThread({ source: "github" })
    const noSource = makeThread({ source: undefined })
    expect(
      filterThreads([gh, noSource], filters({ sources: ["dashboard"] }))
    ).toEqual([noSource])
    expect(
      filterThreads([gh, noSource], filters({ sources: ["github"] }))
    ).toEqual([gh])
  })
})

it("retains a nonmatching coordinator as context for matching workers", () => {
  const match = makeThread({ source: "dashboard" })
  const sibling = makeThread({ source: "github" })
  const parent = makeThread({ source: "slack", taskWorkers: [match, sibling] })
  expect(filterThreads([parent], filters({ sources: ["dashboard"] }))).toEqual([
    { ...parent, taskWorkers: [match] },
  ])
})

describe("hasActiveFilters", () => {
  it("is false for defaults", () => {
    expect(hasActiveFilters(DEFAULT_SIDEBAR_FILTERS)).toBe(false)
  })

  it("is true when any dimension changes", () => {
    expect(hasActiveFilters(filters({ sources: ["github"] }))).toBe(true)
    expect(hasActiveFilters(filters({ includeAutomations: true }))).toBe(true)
    expect(hasActiveFilters(filters({ includeResolved: true }))).toBe(true)
  })
})
