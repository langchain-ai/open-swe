import { describe, expect, it } from "vitest"

import type { AgentThread } from "./types"
import {
  cloudSidebarThread,
  groupSidebarThreadsByTime,
  isSidebarConversation,
  groupRepoGroupsByWorkspace,
  sortSidebarThreads,
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

describe("detailed sidebar", () => {
  it("keeps unknown or coding threads detailed, including changes without a PR", () => {
    const conversation = cloudThread({
      source: "slack",
      status: "finished",
      sandboxId: null,
    })
    expect(isSidebarConversation(cloudSidebarThread(conversation))).toBe(true)
    for (const patch of [
      { sandboxId: undefined },
      { sandboxId: "sandbox" },
      { status: "running" as const },
      { diffStats: { files: 1, additions: 3, deletions: 0 } },
      { attentionReason: "Approve changes" },
      { taskWorkers: [cloudThread()] },
      {
        pr: {
          number: 1,
          title: "Work",
          state: "open" as const,
          headRef: "feature",
          baseRef: "main",
          url: "https://github.com/acme/app/pull/1",
        },
      },
    ]) {
      expect(
        isSidebarConversation(cloudSidebarThread({ ...conversation, ...patch }))
      ).toBe(false)
    }
  })

  it("uses non-overlapping rolling time groups and the selected sort timestamp", () => {
    const day = 86_400_000
    const now = 100 * day
    const items = [0, 1, 7, 30].map((age) =>
      cloudSidebarThread(
        cloudThread({
          id: String(age),
          createdAt: now - age * day,
          updatedAt: now,
        })
      )
    )
    expect(
      groupSidebarThreadsByTime(items, "created", now).map((bucket) => [
        bucket.label,
        bucket.items.map((item) => item.id),
      ])
    ).toEqual([
      ["Last day", ["0"]],
      ["Last 7 days", ["1"]],
      ["Last 30 days", ["7"]],
      ["Older", ["30"]],
    ])
    expect(
      groupSidebarThreadsByTime(items, "updated", now).map(
        (bucket) => bucket.items.length
      )
    ).toEqual([4])
  })
})
