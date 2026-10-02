import { describe, expect, it } from "vitest"

import type { AgentThread } from "./types"
import {
  cloudSidebarThread,
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
  it("sorts chats by creation time without moving recently updated chats", () => {
    const olderUpdated = cloudSidebarThread(
      cloudThread({ id: "older-updated", createdAt: 10, updatedAt: 50 })
    )
    const newer = cloudSidebarThread(
      cloudThread({ id: "newer", createdAt: 20, updatedAt: 20 })
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
    const alpha = cloudSidebarThread(
      cloudThread({
        id: "alpha",
        repo: "alpha",
        repoFullName: "acme/alpha",
        updatedAt: 10,
      })
    )
    const beta = cloudSidebarThread(
      cloudThread({
        id: "beta",
        repo: "beta",
        repoFullName: "acme/beta",
        updatedAt: 20,
      })
    )
    const gamma = cloudSidebarThread(
      cloudThread({
        id: "gamma",
        repo: "gamma",
        repoFullName: "acme/gamma",
        updatedAt: 30,
      })
    )
    const groups = [alpha, beta, gamma].map((thread) => ({
      key: thread.repoKey ?? "",
      label: thread.repoLabel ?? "",
      threads: [thread],
    }))
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
    const thread = cloudSidebarThread(
      cloudThread({ id: "solo", repo: "solo", repoFullName: "acme/solo" })
    )
    const repos = [{ key: "repo:acme/solo", label: "solo", workspace: "ghost" }]

    const grouped = groupRepoGroupsByWorkspace(
      [{ key: "repo:acme/solo", label: "solo", threads: [thread] }],
      repos,
      []
    )

    expect(grouped).toEqual([
      {
        slug: "ghost",
        name: "ghost",
        repos: [expect.objectContaining({ key: "repo:acme/solo" })],
      },
    ])
  })
})
