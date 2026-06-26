import { describe, expect, it } from "vitest"

import {
  DEFAULT_SIDEBAR_FILTERS,
  availableFacets,
  filterThreads,
  groupThreadsByMode,
  hasActiveFilters,
  toggleArrayValue,
} from "./sidebarFilter"
import type { SidebarFilters } from "./sidebarFilter"
import type { AgentThread } from "./types"

const DAY = 24 * 60 * 60 * 1000

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
    isOwner: true,
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
  it("returns all threads with default filters", () => {
    const threads = [makeThread(), makeThread()]
    expect(filterThreads(threads, DEFAULT_SIDEBAR_FILTERS)).toHaveLength(2)
  })

  it("filters by ownership", () => {
    const mine = makeThread({ isOwner: true })
    const shared = makeThread({ isOwner: false })
    const unknown = makeThread({ isOwner: undefined })
    const all = [mine, shared, unknown]
    expect(filterThreads(all, filters({ ownership: "mine" }))).toEqual([
      mine,
      unknown,
    ])
    expect(filterThreads(all, filters({ ownership: "shared" }))).toEqual([
      shared,
    ])
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

  it("filters by model and repo", () => {
    const a = makeThread({ model: "gpt-5", repoFullName: "acme/a" })
    const b = makeThread({ model: "claude", repoFullName: "acme/b" })
    expect(filterThreads([a, b], filters({ models: ["claude"] }))).toEqual([b])
    expect(filterThreads([a, b], filters({ repos: ["acme/a"] }))).toEqual([a])
  })
})

describe("availableFacets", () => {
  it("returns distinct sorted models and repos, skipping empties", () => {
    const threads = [
      makeThread({ model: "gpt-5", repoFullName: "acme/b" }),
      makeThread({ model: "claude", repoFullName: "acme/a" }),
      makeThread({ model: "gpt-5", repoFullName: "" }),
    ]
    const facets = availableFacets(threads)
    expect(facets.models).toEqual(["claude", "gpt-5"])
    expect(facets.repos).toEqual(["acme/a", "acme/b"])
  })
})

describe("groupThreadsByMode", () => {
  it("returns an empty array for no threads", () => {
    expect(groupThreadsByMode([], "date")).toEqual([])
  })

  it("groups everything into one section for 'none'", () => {
    const sections = groupThreadsByMode([makeThread(), makeThread()], "none")
    expect(sections).toHaveLength(1)
    expect(sections[0]?.key).toBe("all")
    expect(sections[0]?.threads).toHaveLength(2)
  })

  it("buckets by date and drops empty buckets", () => {
    const now = Date.now()
    const sections = groupThreadsByMode(
      [
        makeThread({ updatedAt: now }),
        makeThread({ updatedAt: now - 3 * DAY }),
        makeThread({ updatedAt: now - 40 * DAY }),
      ],
      "date"
    )
    expect(sections.map((s) => s.key)).toEqual(["today", "last7", "older"])
    expect(sections.find((s) => s.key === "last7")?.defaultCollapsed).toBe(true)
    expect(sections.find((s) => s.key === "today")?.defaultCollapsed).toBe(
      false
    )
  })

  it("groups by status in a fixed order", () => {
    const sections = groupThreadsByMode(
      [
        makeThread({ status: "idle" }),
        makeThread({ status: "running" }),
        makeThread({ status: "error" }),
      ],
      "status"
    )
    expect(sections.map((s) => s.key)).toEqual(["running", "error", "idle"])
  })

  it("groups by repo alphabetically with a fallback label", () => {
    const sections = groupThreadsByMode(
      [
        makeThread({ repoFullName: "acme/z" }),
        makeThread({ repoFullName: "acme/a" }),
        makeThread({ repoFullName: "" }),
      ],
      "repo"
    )
    expect(sections.map((s) => s.label)).toEqual([
      "acme/a",
      "acme/z",
      "No repository",
    ])
  })

  it("sorts threads within a section by recency", () => {
    const older = makeThread({ status: "idle", updatedAt: 1 })
    const newer = makeThread({ status: "idle", updatedAt: 2 })
    const [section] = groupThreadsByMode([older, newer], "status")
    expect(section?.threads).toEqual([newer, older])
  })
})

describe("hasActiveFilters", () => {
  it("is false for defaults", () => {
    expect(hasActiveFilters(DEFAULT_SIDEBAR_FILTERS)).toBe(false)
  })

  it("is true when any dimension changes", () => {
    expect(hasActiveFilters(filters({ ownership: "mine" }))).toBe(true)
    expect(hasActiveFilters(filters({ statuses: ["running"] }))).toBe(true)
    expect(hasActiveFilters(filters({ includeResolved: false }))).toBe(true)
  })
})

describe("toggleArrayValue", () => {
  it("adds a missing value and removes a present one", () => {
    expect(toggleArrayValue(["a"], "b")).toEqual(["a", "b"])
    expect(toggleArrayValue(["a", "b"], "a")).toEqual(["b"])
  })
})
