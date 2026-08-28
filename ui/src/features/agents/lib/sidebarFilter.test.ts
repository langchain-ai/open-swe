import { describe, expect, it } from "vitest"

import {
  DEFAULT_SIDEBAR_FILTERS,
  availableFacets,
  filterThreads,
  hasActiveFilters,
  toggleArrayValue,
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

  it("filters by status (multi-select)", () => {
    const running = makeThread({ status: "running" })
    const finished = makeThread({ status: "finished" })
    const idle = makeThread({ status: "idle" })
    const result = filterThreads(
      [running, finished, idle],
      filters({ statuses: ["running", "finished"] })
    )
    expect(result).toEqual([running, finished])
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

  it("filters by run location, defaulting cloud summaries to cloud", () => {
    const cloud = makeThread()
    const local = { ...makeThread(), location: "local" as const }
    expect(
      filterThreads([cloud, local], filters({ locations: ["local"] }))
    ).toEqual([local])
    expect(
      filterThreads([cloud, local], filters({ locations: ["cloud"] }))
    ).toEqual([cloud])
  })

  it("filters by pull-request state including 'none'", () => {
    const open = makeThread({
      pr: {
        number: 1,
        title: "x",
        state: "open",
        headRef: "h",
        baseRef: "main",
        url: "u",
      },
    })
    const noPr = makeThread({ pr: undefined })
    expect(filterThreads([open, noPr], filters({ pr: ["open"] }))).toEqual([
      open,
    ])
    expect(filterThreads([open, noPr], filters({ pr: ["none"] }))).toEqual([
      noPr,
    ])
  })

  it("filters by model", () => {
    const a = makeThread({ model: "gpt-5", repoFullName: "acme/a" })
    const b = makeThread({ model: "claude", repoFullName: "acme/b" })
    expect(filterThreads([a, b], filters({ models: ["claude"] }))).toEqual([b])
  })
})

describe("availableFacets", () => {
  it("returns distinct sorted models", () => {
    const threads = [
      makeThread({ model: "gpt-5", repoFullName: "acme/b" }),
      makeThread({ model: "claude", repoFullName: "acme/a" }),
      makeThread({ model: "gpt-5", repoFullName: "" }),
    ]
    const facets = availableFacets(threads)
    expect(facets.models).toEqual(["claude", "gpt-5"])
  })
})

describe("hasActiveFilters", () => {
  it("is false for defaults", () => {
    expect(hasActiveFilters(DEFAULT_SIDEBAR_FILTERS)).toBe(false)
  })

  it("is true when any dimension changes", () => {
    expect(hasActiveFilters(filters({ statuses: ["running"] }))).toBe(true)
    expect(hasActiveFilters(filters({ includeAutomations: true }))).toBe(true)
    expect(hasActiveFilters(filters({ includeResolved: true }))).toBe(true)
  })
})

describe("toggleArrayValue", () => {
  it("adds a missing value and removes a present one", () => {
    expect(toggleArrayValue(["a"], "b")).toEqual(["a", "b"])
    expect(toggleArrayValue(["a", "b"], "a")).toEqual(["b"])
  })
})
