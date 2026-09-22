import { describe, expect, it } from "vitest"

import type { DesktopLocalThreadSummary, DesktopProject } from "@/desktop"
import type { AgentThread } from "./types"
import {
  applyRepoKeyAliases,
  cloudRepoKeysByLabel,
  cloudSidebarThread,
  groupSidebarThreadsByRepo,
  groupSidebarThreadsByWorkspace,
  localSidebarThread,
  sidebarRepoOptions,
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

function localThread(
  overrides: Partial<DesktopLocalThreadSummary> = {}
): DesktopLocalThreadSummary {
  return {
    id: "same-id",
    cwd: "/Users/example/open-swe",
    worktreePath: null,
    title: "Local thread",
    viewed: true,
    createdAt: 10,
    updatedAt: 30,
    modelId: "gpt-5",
    effort: "medium",
    ...overrides,
  }
}

const checkout: DesktopProject = {
  cwd: "/Users/example/open-swe",
  name: "open-swe",
  addedAt: 1,
  scopeId: "project-open-swe",
}

describe("sidebar thread adapters", () => {
  it("uses short repository names and namespaced identities", () => {
    const cloud = cloudSidebarThread(cloudThread())
    const local = localSidebarThread(localThread(), checkout, undefined)

    expect(cloud).toMatchObject({
      key: "cloud:same-id",
      repoKey: "repo:langchain-ai/open-swe",
      repoLabel: "open-swe",
    })
    expect(local).toMatchObject({
      key: "local:same-id",
      repoKey: "repo:/users/example/open-swe",
      repoLabel: "open-swe",
    })
  })

  it("normalizes local activity into shared statuses", () => {
    expect(localSidebarThread(localThread(), checkout, "running").status).toBe(
      "running"
    )
    expect(localSidebarThread(localThread(), checkout, "error").status).toBe(
      "error"
    )
    expect(
      localSidebarThread(localThread({ viewed: false }), checkout, undefined)
        .status
    ).toBe("finished")
    expect(localSidebarThread(localThread(), checkout, undefined).status).toBe(
      "idle"
    )
  })

  it("merges a local checkout into the cloud repository of the same name", () => {
    const cloud = [cloudSidebarThread(cloudThread())]
    const threads = [
      ...cloud,
      ...applyRepoKeyAliases(
        [localSidebarThread(localThread(), checkout, undefined)],
        cloudRepoKeysByLabel(cloud)
      ),
    ]

    expect(sidebarRepoOptions(threads, [])).toEqual([
      { key: "repo:langchain-ai/open-swe", label: "open-swe" },
    ])
  })

  it("keeps same-named repositories from different owners apart", () => {
    const acme = cloudSidebarThread(
      cloudThread({ id: "a", repo: "api", repoFullName: "acme/api" })
    )
    const other = cloudSidebarThread(
      cloudThread({ id: "b", repo: "api", repoFullName: "other/api" })
    )

    expect(acme.repoKey).not.toBe(other.repoKey)
    expect(sidebarRepoOptions([acme, other], [])).toHaveLength(2)
    // The label is ambiguous, so a local "api" must not be folded into either.
    const local = localSidebarThread(
      localThread({ cwd: "/Users/example/api" }),
      {
        cwd: "/Users/example/api",
        name: "api",
        addedAt: 1,
        scopeId: "project-api",
      },
      undefined
    )
    const aliases = cloudRepoKeysByLabel([acme, other])
    expect(applyRepoKeyAliases([local], aliases)[0]?.repoKey).toBe(
      local.repoKey
    )
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

describe("groupSidebarThreadsByRepo", () => {
  it("buckets threads per repository and ranks repositories by their freshest thread", () => {
    const alphaOld = cloudSidebarThread(
      cloudThread({
        id: "alpha-old",
        repo: "alpha",
        repoFullName: "acme/alpha",
        updatedAt: 5,
      })
    )
    const alphaNew = cloudSidebarThread(
      cloudThread({
        id: "alpha-new",
        repo: "alpha",
        repoFullName: "acme/alpha",
        updatedAt: 40,
      })
    )
    const beta = cloudSidebarThread(
      cloudThread({
        id: "beta",
        repo: "beta",
        repoFullName: "acme/beta",
        updatedAt: 50,
      })
    )
    const items = [alphaOld, alphaNew, beta]

    const grouped = groupSidebarThreadsByRepo(
      items,
      sidebarRepoOptions(items, [])
    )

    expect(grouped.repos.map((group) => group.label)).toEqual(["beta", "alpha"])
    expect(grouped.repos[1]?.threads.map((thread) => thread.id)).toEqual([
      "alpha-new",
      "alpha-old",
    ])
    expect(grouped.recents).toEqual([])
  })

  it("sends threads with no known repository to Recents", () => {
    const orphan = cloudSidebarThread(
      cloudThread({ id: "orphan", repo: "", repoFullName: "" })
    )
    const known = cloudSidebarThread(
      cloudThread({ id: "known", repo: "alpha", repoFullName: "acme/alpha" })
    )

    const grouped = groupSidebarThreadsByRepo(
      [orphan, known],
      sidebarRepoOptions([known], [])
    )

    expect(grouped.repos).toHaveLength(1)
    expect(grouped.recents.map((thread) => thread.id)).toEqual(["orphan"])
  })
})

describe("groupSidebarThreadsByWorkspace", () => {
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
    const threads = [alpha, beta, gamma]
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

    const grouped = groupSidebarThreadsByWorkspace(threads, repos, workspaces)

    expect(grouped.recents).toEqual([])
    expect(
      grouped.workspaces.map((workspace) => [
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
    expect(grouped.workspaces).toHaveLength(2)
  })

  it("falls back to the slug as a display name for an unknown workspace", () => {
    const thread = cloudSidebarThread(
      cloudThread({ id: "solo", repo: "solo", repoFullName: "acme/solo" })
    )
    const repos = [{ key: "repo:acme/solo", label: "solo", workspace: "ghost" }]

    const grouped = groupSidebarThreadsByWorkspace([thread], repos, [])

    expect(grouped.workspaces).toEqual([
      {
        slug: "ghost",
        name: "ghost",
        repos: [expect.objectContaining({ key: "repo:acme/solo" })],
      },
    ])
  })
})
