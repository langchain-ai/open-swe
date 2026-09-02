import { describe, expect, it } from "vitest"

import {
  DEFAULT_SIDEBAR_FILTERS,
  filterThreads,
  hasActiveFilters,
} from "./sidebarFilter"
import type { SidebarFilters } from "./sidebarFilter"
import type { AgentThread } from "./types"

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
  it("returns ordinary threads with default filters", () => {
    const threads = [
      makeThread(),
      makeThread({ source: "schedule", threadCategory: "automation" }),
    ]
    expect(filterThreads(threads, DEFAULT_SIDEBAR_FILTERS)).toHaveLength(1)
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
