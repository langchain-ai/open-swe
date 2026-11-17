import { describe, expect, it } from "vitest"

import {
  DEFAULT_SIDEBAR_FILTERS,
  filterThreads,
  hasActiveFilters,
} from "./sidebarFilter"
import {
  cloudSidebarThread,
  groupSidebarThreadsByRecency,
} from "./sidebarThreads"
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
    ownerId: "alice-id",
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
    expect(
      filterThreads(threads, DEFAULT_SIDEBAR_FILTERS, undefined, "alice-id")
    ).toHaveLength(1)
  })

  it("keeps the actively viewed thread visible despite source filters", () => {
    const active = makeThread({
      id: "active-thread",
      source: "slack",
    })
    const hidden = makeThread({ source: "slack" })
    const visible = makeThread({ source: "github" })

    expect(
      filterThreads(
        [active, hidden, visible],
        filters({ sources: ["github"] }),
        active.id,
        "alice-id"
      )
    ).toEqual([active, visible])
  })

  it("hides archived threads even when actively viewed unless Show archived is enabled", () => {
    const archived = makeThread({ id: "archived", resolved: true })
    const ordinary = makeThread()
    const threads = [archived, ordinary]

    expect(filterThreads(threads, filters(), archived.id, "alice-id")).toEqual([
      ordinary,
    ])
    expect(
      filterThreads(
        threads,
        filters({ includeResolved: true }),
        archived.id,
        "alice-id"
      )
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
        filters({ includeAutomations: true }),
        undefined,
        "alice-id"
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
      filterThreads(
        [ordinary, automation],
        filters({ sources: ["schedule"] }),
        undefined,
        "alice-id"
      )
    ).toEqual([automation])
  })

  it("hides Slack threads even when actively viewed and running", () => {
    const slack = makeThread({ source: "slack", status: "running" })
    const movedToWeb = makeThread({ source: "dashboard", status: "running" })
    const github = makeThread({ source: "github" })
    const threads = [slack, movedToWeb, github]
    expect(
      filterThreads(
        threads,
        filters({ includeSlack: false }),
        slack.id,
        "alice-id"
      )
    ).toEqual([movedToWeb, github])
    expect(
      filterThreads(
        threads,
        filters({ includeSlack: true }),
        slack.id,
        "alice-id"
      )
    ).toEqual(threads)
  })

  it("hides the active Claude Code thread until Show Claude Code is enabled", () => {
    const active = makeThread({ source: "claude_code", status: "running" })
    const ordinary = makeThread()
    const threads = [active, ordinary]
    expect(
      filterThreads(
        threads,
        filters({ includeClaudeCode: false }),
        active.id,
        "alice-id"
      )
    ).toEqual([ordinary])
    expect(filterThreads(threads, filters(), active.id, "alice-id")).toEqual(
      threads
    )
  })

  it("filters active automations before grouping threads by activity", () => {
    const active = makeThread({ source: "schedule", status: "running" })
    const delegated = makeThread({
      threadCategory: "automation",
      waitingForDelegatedWork: true,
    })
    const ordinary = makeThread({ status: "running" })
    const items = [active, delegated, ordinary].map(cloudSidebarThread)
    expect(
      groupSidebarThreadsByRecency(
        filterThreads(items, filters(), active.id, "alice-id")
      ).map((group) => [group.label, group.threads.map((thread) => thread.id)])
    ).toEqual([["Today", [ordinary.id]]])
    expect(
      filterThreads(
        items,
        filters({ includeAutomations: true }),
        active.id,
        "alice-id"
      )
    ).toEqual(items)
  })

  it("does not reinsert another person's active thread with Only my threads enabled", () => {
    const active = makeThread({
      ownerId: "bob-id",
      ownerLogin: "alice",
      status: "running",
    })
    const owned = makeThread({ ownerLogin: "old-alice-login" })
    const unowned = makeThread({ ownerId: undefined })
    const items = [active, owned, unowned].map(cloudSidebarThread)
    expect(filterThreads(items, filters(), active.id, "alice-id")).toEqual([
      items[1],
    ])
    expect(
      filterThreads(items, filters({ ownedOnly: false }), active.id, "alice-id")
    ).toEqual(items)
  })

  it("does not restore checkbox-hidden coordinators or workers as task context", () => {
    const worker = makeThread({ status: "running" })
    const hidden = makeThread({
      source: "slack",
      taskWorkers: [worker],
      waitingForDelegatedWork: true,
    })
    const hiddenWorker = makeThread({ source: "claude_code" })
    const visible = makeThread({ taskWorkers: [worker, hiddenWorker] })
    expect(
      filterThreads(
        [hidden, visible],
        filters({ includeSlack: false, includeClaudeCode: false }),
        worker.id,
        "alice-id"
      )
    ).toEqual([{ ...visible, taskWorkers: [worker] }])
  })

  it("filters by source, defaulting missing source to dashboard", () => {
    const gh = makeThread({ source: "github" })
    const noSource = makeThread({ source: undefined })
    expect(
      filterThreads(
        [gh, noSource],
        filters({ sources: ["dashboard"] }),
        undefined,
        "alice-id"
      )
    ).toEqual([noSource])
    expect(
      filterThreads(
        [gh, noSource],
        filters({ sources: ["github"] }),
        undefined,
        "alice-id"
      )
    ).toEqual([gh])
  })
})

it("retains a nonmatching coordinator as context for matching workers", () => {
  const match = makeThread({ source: "dashboard" })
  const sibling = makeThread({ source: "github" })
  const parent = makeThread({ source: "slack", taskWorkers: [match, sibling] })
  expect(
    filterThreads(
      [parent],
      filters({ sources: ["dashboard"] }),
      undefined,
      "alice-id"
    )
  ).toEqual([{ ...parent, taskWorkers: [match] }])
})

describe("hasActiveFilters", () => {
  it("treats owned-only as filtering even when it is the default", () => {
    expect(hasActiveFilters(DEFAULT_SIDEBAR_FILTERS)).toBe(true)
    expect(hasActiveFilters(filters({ ownedOnly: false }))).toBe(false)
  })

  it("still detects other filters when showing all owners", () => {
    expect(
      hasActiveFilters(filters({ ownedOnly: false, includeSlack: false }))
    ).toBe(true)
    expect(
      hasActiveFilters(filters({ ownedOnly: false, sources: ["github"] }))
    ).toBe(true)
    expect(
      hasActiveFilters(filters({ ownedOnly: false, includeAutomations: true }))
    ).toBe(true)
    expect(
      hasActiveFilters(filters({ ownedOnly: false, includeResolved: true }))
    ).toBe(true)
  })
})
