import { describe, expect, it } from "vitest"

import {
  DEFAULT_SIDEBAR_FILTERS,
  GROUP_MODE_OPTIONS,
  availableFacets,
  filterThreads,
  groupThreadsByMode,
  hasActiveFilters,
  reconcilePinnedAttentionThread,
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
  it("offers focus grouping in the sidebar", () => {
    expect(GROUP_MODE_OPTIONS).toContainEqual({
      value: "focus",
      label: "Focus",
    })
  })

  it("returns an empty array for no threads", () => {
    expect(groupThreadsByMode([], "date")).toEqual([])
  })

  it("groups everything into one section for 'none'", () => {
    const sections = groupThreadsByMode([makeThread(), makeThread()], "none")
    expect(sections).toHaveLength(1)
    expect(sections[0]?.key).toBe("all")
    expect(sections[0]?.threads).toHaveLength(2)
  })

  it("uses the board focus definitions", () => {
    const sections = groupThreadsByMode(
      [
        makeThread({ id: "resolved", resolved: true, status: "running" }),
        makeThread({ id: "running", status: "running" }),
        makeThread({ id: "error", status: "error" }),
        makeThread({ id: "interrupted", status: "interrupted" }),
        makeThread({ id: "plan-ready", planStatus: "ready" }),
        makeThread({ id: "plan-shared", planStatus: "shared" }),
        makeThread({ id: "unread", status: "finished", viewed: false }),
        makeThread({ id: "finished", status: "finished" }),
        makeThread({ id: "idle", status: "idle" }),
      ],
      "focus"
    )

    expect(sections.map((section) => section.key)).toEqual([
      "attention",
      "progress",
      "ready",
      "done",
    ])
    expect(
      Object.fromEntries(
        sections.map((section) => [
          section.key,
          section.threads.map((thread) => thread.id).sort(),
        ])
      )
    ).toEqual({
      attention: [
        "error",
        "interrupted",
        "plan-ready",
        "plan-shared",
        "unread",
      ],
      progress: ["running"],
      ready: ["finished", "idle"],
      done: ["resolved"],
    })
  })

  it("keeps the active finished thread in attention after it is viewed", () => {
    const pinnedThread = makeThread({
      id: "active",
      status: "finished",
      viewed: false,
    })
    const sections = groupThreadsByMode(
      [{ ...pinnedThread, title: "Fresh title", viewed: true }],
      "focus",
      pinnedThread
    )

    expect(sections).toHaveLength(1)
    expect(sections[0]?.key).toBe("attention")
    expect(sections[0]?.threads[0]).toMatchObject({
      id: "active",
      title: "Fresh title",
      viewed: true,
    })
  })

  it("captures an asynchronously loaded active attention thread", () => {
    const activeThread = makeThread({ id: "active", viewed: false })

    expect(
      reconcilePinnedAttentionThread(undefined, "active", undefined)
    ).toBeUndefined()
    expect(
      reconcilePinnedAttentionThread(undefined, "active", activeThread)
    ).toBe(activeThread)
    expect(
      reconcilePinnedAttentionThread(activeThread, "active", undefined)
    ).toBe(activeThread)
    expect(
      reconcilePinnedAttentionThread(activeThread, "other", undefined)
    ).toBeUndefined()
  })

  it("buckets by date and drops empty buckets", () => {
    const now = Date.now()
    const sections = groupThreadsByMode(
      [
        makeThread({ createdAt: now }),
        makeThread({ createdAt: now - 3 * DAY }),
        makeThread({ createdAt: now - 40 * DAY }),
      ],
      "date"
    )
    expect(sections.map((s) => s.key)).toEqual(["today", "last7", "older"])
    expect(sections.find((s) => s.key === "last7")?.defaultCollapsed).toBe(
      false
    )
    expect(sections.find((s) => s.key === "today")?.defaultCollapsed).toBe(
      false
    )
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

  it("sorts threads within a section by creation time", () => {
    const older = makeThread({ createdAt: 1, updatedAt: 3 })
    const newer = makeThread({ createdAt: 2, updatedAt: 2 })
    const [section] = groupThreadsByMode([older, newer], "none")
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
