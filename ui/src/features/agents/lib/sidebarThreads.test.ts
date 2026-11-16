import { describe, expect, it } from "vitest"

import type { AgentThread } from "./types"
import {
  cloudSidebarThread,
  groupRepoGroupsByWorkspace,
  groupSidebarThreadsByRecency,
  groupSidebarThreadsByDay,
  sortSidebarThreads,
  sidebarRefreshInterval,
} from "./sidebarThreads"

function cloudThread(overrides: Partial<AgentThread> = {}): AgentThread {
  return {
    id: "same-id",
    title: "Cloud thread",
    repo: "open-swe",
    repoFullName: "langchain-ai/open-swe",
    branch: "main",
    model: "gpt-5",
    status: "idle",
    viewed: true,
    createdAt: 10,
    updatedAt: 20,
    messages: [],
    ...overrides,
  }
}

describe("sidebar thread adapters", () => {
  it("refreshes finished workers while approval is outstanding", () => {
    const worker = cloudThread({
      status: "finished",
      pendingWorkflowApproval: true,
    })
    expect(
      sidebarRefreshInterval([cloudThread({ taskWorkers: [worker] })])
    ).toBe(30_000)
    expect(
      sidebarRefreshInterval([
        cloudThread({
          taskWorkers: [{ ...worker, pendingWorkflowApproval: false }],
        }),
      ])
    ).toBe(false)
  })

  it("uses short repository names and namespaced identities", () => {
    expect(cloudSidebarThread(cloudThread())).toMatchObject({
      key: "cloud:same-id",
      repoKey: "repo:langchain-ai/open-swe",
      repoLabel: "open-swe",
    })
  })

  it("keeps same-named repositories from different owners apart", () => {
    const acme = cloudSidebarThread(
      cloudThread({ id: "a", repo: "api", repoFullName: "acme/api" })
    )
    const other = cloudSidebarThread(
      cloudThread({ id: "b", repo: "api", repoFullName: "other/api" })
    )

    expect(acme.repoKey).not.toBe(other.repoKey)
  })
})

describe("sortSidebarThreads", () => {
  it("sorts chats and task families by creation or update time", () => {
    const olderUpdated = cloudSidebarThread(
      cloudThread({ id: "older-updated", createdAt: 10, updatedAt: 50 })
    )
    const newer = cloudSidebarThread(
      cloudThread({
        id: "newer",
        createdAt: 5,
        updatedAt: 20,
        taskWorkers: [cloudThread({ id: "worker", createdAt: 20 })],
      })
    )

    expect(
      sortSidebarThreads([olderUpdated, newer], "created").map(
        (thread) => thread.id
      )
    ).toEqual(["newer", "older-updated"])
    expect(
      sortSidebarThreads([olderUpdated, newer], "updated").map(
        (thread) => thread.id
      )
    ).toEqual(["older-updated", "newer"])
  })
})

describe("groupSidebarThreadsByDate", () => {
  it("keeps Today and Yesterday separate across midnight", () => {
    const now = new Date(2026, 5, 20, 6)
    const threads = [
      new Date(2026, 5, 20, 5),
      new Date(2026, 5, 19, 23),
      new Date(2026, 5, 19, 18),
      new Date(2026, 5, 19, 17),
      new Date(2026, 5, 19, 16),
      new Date(2026, 5, 19, 15),
    ].map((date, index) =>
      cloudSidebarThread(
        cloudThread({
          id: String(index),
          updatedAt: date.getTime(),
        })
      )
    )

    expect(
      groupSidebarThreadsByRecency(threads, now).map((group) => [
        group.label,
        group.threads.map((thread) => thread.id),
      ])
    ).toEqual([
      ["Today", ["0"]],
      ["Yesterday", ["1", "2", "3", "4", "5"]],
    ])
  })

  it("keeps Yesterday separate across the Monday week and month boundary", () => {
    const now = new Date(2026, 5, 1, 10)
    const threads = [
      new Date(2026, 5, 1, 9),
      new Date(2026, 4, 31, 23),
      new Date(2026, 4, 30, 12),
    ].map((date, index) =>
      cloudSidebarThread(
        cloudThread({ id: String(index), updatedAt: date.getTime() })
      )
    )

    expect(
      groupSidebarThreadsByRecency(threads, now).map((group) => [
        group.label,
        group.threads.map((thread) => thread.id),
      ])
    ).toEqual([
      ["Today", ["0"]],
      ["Yesterday", ["1"]],
      ["Sat, May 30", ["2"]],
    ])
  })

  it("dates errored threads while separating running and delegated work", () => {
    const now = new Date(2026, 5, 20, 15)
    const threads = [
      cloudThread({ id: "running", status: "running" }),
      cloudThread({
        id: "error",
        status: "error",
        updatedAt: now.getTime() - 1000,
      }),
      cloudThread({
        id: "finished",
        status: "finished",
        updatedAt: now.getTime(),
      }),
      cloudThread({
        id: "delegating",
        status: "idle",
        waitingForDelegatedWork: true,
      }),
    ].map(cloudSidebarThread)

    expect(
      groupSidebarThreadsByRecency(threads, now).map((group) => [
        group.label,
        group.threads.map((thread) => thread.id),
      ])
    ).toEqual([
      ["Today", ["finished", "error"]],
      ["Running", ["running", "delegating"]],
    ])
  })

  it("preserves sparse calendar days and sorts by activity rather than creation", () => {
    const now = new Date(2026, 5, 10, 15)
    const at = (day: number, hour: number) =>
      new Date(2026, 5, day, hour).getTime()
    const thread = (id: string, createdAt: number, updatedAt: number) =>
      cloudSidebarThread(cloudThread({ id, createdAt, updatedAt }))

    const groups = groupSidebarThreadsByRecency(
      [
        thread("busy", at(1, 9), at(10, 14)),
        thread("fresh", at(10, 1), at(10, 2)),
        thread("old", at(3, 9), at(5, 10)),
        thread("older", at(2, 9), at(4, 10)),
        thread("yesterday", at(9, 9), at(9, 10)),
      ],
      now
    )

    expect(
      groups.map((group) => [
        group.key,
        group.label,
        group.threads.map((item) => item.id),
      ])
    ).toEqual([
      ["last-12-hours", "Last 12 hours", ["busy"]],
      ["today", "Today", ["fresh"]],
      ["yesterday", "Yesterday", ["yesterday"]],
      ["day:2026-6-5", "Fri, Jun 5", ["old"]],
      ["day:2026-6-4", "Thu, Jun 4", ["older"]],
    ])
  })

  it("keeps the past seven days separate across week boundaries", () => {
    const now = new Date(2026, 5, 20, 15)
    const threads = [20, 19, 18, 17, 16, 15, 14, 7, 6].flatMap((day) =>
      [1, 2, 3].map((hour) =>
        cloudSidebarThread(
          cloudThread({
            id: `${day}-${hour}`,
            updatedAt: new Date(2026, 5, day, hour).getTime(),
          })
        )
      )
    )

    const groups = groupSidebarThreadsByRecency(threads, now)

    expect(groups.map((group) => [group.label, group.threads.length])).toEqual([
      ["Last 12 hours", 1],
      ["Today", 2],
      ["Yesterday", 3],
      ["Thu, Jun 18", 3],
      ["Wed, Jun 17", 3],
      ["Tue, Jun 16", 3],
      ["Mon, Jun 15", 3],
      ["Sun, Jun 14", 3],
      ["Last 14 days", 3],
      ["Older", 3],
    ])
    expect(
      groups.flatMap((group) => group.threads.map((thread) => thread.id))
    ).toEqual(
      [...threads]
        .sort((left, right) => right.updatedAt - left.updatedAt)
        .map((thread) => thread.id)
    )
  })

  it("merges a sparse oldest range with the preceding range", () => {
    const now = new Date(2026, 5, 20, 15)
    const threads = [20, 20, 20, 13, 13, 13, 6].map((day, index) =>
      cloudSidebarThread(
        cloudThread({
          id: String(index),
          updatedAt: new Date(2026, 5, day, 12).getTime(),
        })
      )
    )

    const groups = groupSidebarThreadsByRecency(threads, now)

    expect(groups.map((group) => [group.label, group.threads.length])).toEqual([
      ["Today", 3],
      ["Older", 4],
    ])
  })

  it("keeps a small list in its narrowest matching range and handles no threads", () => {
    const now = new Date(2026, 5, 20, 15)
    const thread = cloudSidebarThread(cloudThread({ updatedAt: now.getTime() }))

    expect(
      groupSidebarThreadsByRecency([thread], now).map((group) => group.label)
    ).toEqual(["Today"])
    expect(groupSidebarThreadsByRecency([], now)).toEqual([])
  })
})

describe("calendar date grouping", () => {
  it("keeps running and delegated sessions in the sticky running group", () => {
    const now = new Date(2026, 5, 20, 15)
    const threads = [
      cloudThread({
        id: "running",
        status: "running",
        updatedAt: new Date(2026, 5, 12).getTime(),
      }),
      cloudThread({
        id: "delegating",
        status: "idle",
        waitingForDelegatedWork: true,
      }),
      cloudThread({ id: "finished", status: "idle", updatedAt: now.getTime() }),
    ].map(cloudSidebarThread)

    expect(
      groupSidebarThreadsByDay(threads, now).map((group) => [
        group.key,
        group.threads.map((thread) => thread.id),
      ])
    ).toEqual([
      ["today", ["finished"]],
      ["status:running", ["running", "delegating"]],
    ])
    expect(groupSidebarThreadsByDay([], now)).toEqual([])
  })

  it("keeps older calendar days separate instead of merging by recency", () => {
    const now = new Date(2026, 5, 20, 15)
    const threads = [12, 11].map((day) =>
      cloudSidebarThread(
        cloudThread({
          id: String(day),
          updatedAt: new Date(2026, 5, day, 12).getTime(),
        })
      )
    )
    expect(
      groupSidebarThreadsByDay(threads, now).map((group) =>
        group.threads.map((thread) => thread.id)
      )
    ).toEqual([["12"], ["11"]])
    expect(
      groupSidebarThreadsByRecency(threads, now).map((group) =>
        group.threads.map((thread) => thread.id)
      )
    ).toEqual([["12", "11"]])
  })
})

describe("groupRepoGroupsByWorkspace", () => {
  it("nests repository groups under their owning workspace, defaulting the rest", () => {
    const groups = [
      { key: "repo:acme/alpha", label: "alpha" },
      { key: "repo:acme/beta", label: "beta" },
      { key: "repo:acme/gamma", label: "gamma" },
    ]
    const repos = [
      { key: "repo:acme/alpha", label: "alpha", workspace: "oss" },
      { key: "repo:acme/beta", label: "beta", workspace: "oss" },
      // No workspace field at all: falls under "default".
      { key: "repo:acme/gamma", label: "gamma" },
    ]
    const workspaces = [
      { slug: "oss", name: "Open Source" },
      { slug: "default", name: "Default" },
    ]

    const grouped = groupRepoGroupsByWorkspace(groups, repos, workspaces)

    expect(
      grouped.map((workspace) => [
        workspace.slug,
        workspace.name,
        workspace.repos.map((repo) => repo.key).sort(),
      ])
    ).toEqual(
      expect.arrayContaining([
        ["oss", "Open Source", ["repo:acme/alpha", "repo:acme/beta"]],
        ["default", "Default", ["repo:acme/gamma"]],
      ])
    )
    expect(grouped).toHaveLength(2)
  })

  it("falls back to the slug as a display name for an unknown workspace", () => {
    const repos = [{ key: "repo:acme/solo", label: "solo", workspace: "ghost" }]

    const grouped = groupRepoGroupsByWorkspace(repos, repos, [])

    expect(grouped).toEqual([
      {
        slug: "ghost",
        name: "ghost",
        repos: [expect.objectContaining({ key: "repo:acme/solo" })],
      },
    ])
  })
})
