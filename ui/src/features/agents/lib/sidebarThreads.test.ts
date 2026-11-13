import { describe, expect, it } from "vitest"

import type { AgentThread } from "./types"
import {
  cloudSidebarThread,
  groupRepoGroupsByWorkspace,
  groupSidebarThreadsByDate,
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
  it("groups the rolling 12-hour window across midnight without duplicating Today", () => {
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
      groupSidebarThreadsByDate(threads, now).map((group) => [
        group.label,
        group.threads.map((thread) => thread.id),
      ])
    ).toEqual([
      ["Last 12 hours", ["0", "1", "2"]],
      ["Yesterday", ["3", "4", "5"]],
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
      groupSidebarThreadsByDate(threads, now).map((group) => [
        group.label,
        group.threads.map((thread) => thread.id),
      ])
    ).toEqual([
      ["Last 12 hours", ["finished", "error"]],
      ["Running", ["running", "delegating"]],
    ])
  })

  it("merges sparse recent days and sorts by activity rather than creation", () => {
    const now = new Date(2026, 5, 10, 15)
    const at = (day: number, hour: number) =>
      new Date(2026, 5, day, hour).getTime()
    const thread = (id: string, createdAt: number, updatedAt: number) =>
      cloudSidebarThread(cloudThread({ id, createdAt, updatedAt }))

    const groups = groupSidebarThreadsByDate(
      [
        thread("busy", at(1, 9), at(10, 14)),
        thread("fresh", at(10, 8), at(10, 9)),
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
      [
        "last-7-days",
        "Last 7 days",
        ["busy", "fresh", "yesterday", "old", "older"],
      ],
    ])
  })

  it("caps dense ranges at four groups and includes the oldest threads", () => {
    const now = new Date(2026, 5, 20, 15)
    const threads = [20, 19, 14, 7, 6].flatMap((day) =>
      [1, 2, 3].map((hour) =>
        cloudSidebarThread(
          cloudThread({
            id: `${day}-${hour}`,
            updatedAt: new Date(2026, 5, day, hour).getTime(),
          })
        )
      )
    )

    const groups = groupSidebarThreadsByDate(threads, now)

    expect(groups.map((group) => [group.label, group.threads.length])).toEqual([
      ["Today", 3],
      ["Yesterday", 3],
      ["Last 7 days", 3],
      ["Older", 6],
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
    const threads = [20, 20, 20, 14, 14, 14, 6].map((day, index) =>
      cloudSidebarThread(
        cloudThread({
          id: String(index),
          updatedAt: new Date(2026, 5, day, 12).getTime(),
        })
      )
    )

    const groups = groupSidebarThreadsByDate(threads, now)

    expect(groups.map((group) => [group.label, group.threads.length])).toEqual([
      ["Last 12 hours", 3],
      ["Older", 4],
    ])
  })

  it("keeps a small list in its narrowest matching range and handles no threads", () => {
    const now = new Date(2026, 5, 20, 15)
    const thread = cloudSidebarThread(cloudThread({ updatedAt: now.getTime() }))

    expect(
      groupSidebarThreadsByDate([thread], now).map((group) => group.label)
    ).toEqual(["Last 12 hours"])
    expect(groupSidebarThreadsByDate([], now)).toEqual([])
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
