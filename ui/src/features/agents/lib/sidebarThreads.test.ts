import { describe, expect, it } from "vitest"

import type { DesktopLocalThreadSummary, DesktopProject } from "@/desktop"
import type { AgentThread } from "./types"
import {
  applyProjectKeyAliases,
  cloudProjectKeysByLabel,
  cloudSidebarThread,
  groupSidebarThreadsByProject,
  localSidebarThread,
  sidebarProjectOptions,
  sortSidebarThreads,
} from "./sidebarThreads"

function cloudThread(overrides: Partial<AgentThread> = {}): AgentThread {
  return {
    id: "same-id",
    title: "Cloud thread",
    repos: ["langchain-ai/open-swe"],
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

const project: DesktopProject = {
  cwd: "/Users/example/open-swe",
  name: "open-swe",
  addedAt: 1,
  scopeId: "project-open-swe",
}

describe("sidebar thread adapters", () => {
  it("uses short repository names and namespaced identities", () => {
    const cloud = cloudSidebarThread(cloudThread())
    const local = localSidebarThread(localThread(), project, undefined)

    expect(cloud).toMatchObject({
      key: "cloud:same-id",
      projectKeys: ["project:langchain-ai/open-swe"],
      projectLabels: ["open-swe"],
    })
    expect(local).toMatchObject({
      key: "local:same-id",
      projectKeys: ["project:/users/example/open-swe"],
      projectLabels: ["open-swe"],
    })
  })

  it("normalizes local activity into shared statuses", () => {
    expect(localSidebarThread(localThread(), project, "running").status).toBe(
      "running"
    )
    expect(localSidebarThread(localThread(), project, "error").status).toBe(
      "error"
    )
    expect(
      localSidebarThread(localThread({ viewed: false }), project, undefined)
        .status
    ).toBe("finished")
    expect(localSidebarThread(localThread(), project, undefined).status).toBe(
      "idle"
    )
  })

  it("merges a local checkout into the cloud project of the same name", () => {
    const cloud = [cloudSidebarThread(cloudThread())]
    const threads = [
      ...cloud,
      ...applyProjectKeyAliases(
        [localSidebarThread(localThread(), project, undefined)],
        cloudProjectKeysByLabel(cloud)
      ),
    ]

    expect(sidebarProjectOptions(threads, [])).toEqual([
      { key: "project:langchain-ai/open-swe", label: "open-swe" },
    ])
  })

  it("keeps same-named repositories from different owners apart", () => {
    const acme = cloudSidebarThread(
      cloudThread({ id: "a", repos: ["acme/api"] })
    )
    const other = cloudSidebarThread(
      cloudThread({ id: "b", repos: ["other/api"] })
    )

    expect(acme.projectKeys).not.toEqual(other.projectKeys)
    expect(sidebarProjectOptions([acme, other], [])).toHaveLength(2)
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
    const aliases = cloudProjectKeysByLabel([acme, other])
    expect(applyProjectKeyAliases([local], aliases)[0]?.projectKeys).toEqual(
      local.projectKeys
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

describe("groupSidebarThreadsByProject", () => {
  it("buckets threads per project and ranks projects by their freshest thread", () => {
    const alphaOld = cloudSidebarThread(
      cloudThread({
        id: "alpha-old",
        repos: ["acme/alpha"],
        updatedAt: 5,
      })
    )
    const alphaNew = cloudSidebarThread(
      cloudThread({
        id: "alpha-new",
        repos: ["acme/alpha"],
        updatedAt: 40,
      })
    )
    const beta = cloudSidebarThread(
      cloudThread({
        id: "beta",
        repos: ["acme/beta"],
        updatedAt: 50,
      })
    )
    const items = [alphaOld, alphaNew, beta]

    const grouped = groupSidebarThreadsByProject(
      items,
      sidebarProjectOptions(items, [])
    )

    expect(grouped.projects.map((group) => group.label)).toEqual([
      "beta",
      "alpha",
    ])
    expect(grouped.projects[1]?.threads.map((thread) => thread.id)).toEqual([
      "alpha-new",
      "alpha-old",
    ])
    expect(grouped.recents).toEqual([])
  })

  it("lists a multi-repo thread under every project it targets", () => {
    const shared = cloudSidebarThread(
      cloudThread({ id: "shared", repos: ["acme/alpha", "acme/beta"] })
    )

    const grouped = groupSidebarThreadsByProject(
      [shared],
      sidebarProjectOptions([shared], [])
    )

    expect(
      grouped.projects.map((group) => [
        group.label,
        group.threads.map((thread) => thread.id),
      ])
    ).toEqual([
      ["alpha", ["shared"]],
      ["beta", ["shared"]],
    ])
    expect(grouped.recents).toEqual([])
  })

  it("sends threads with no known project to Recents", () => {
    const orphan = cloudSidebarThread(cloudThread({ id: "orphan", repos: [] }))
    const known = cloudSidebarThread(
      cloudThread({ id: "known", repos: ["acme/alpha"] })
    )

    const grouped = groupSidebarThreadsByProject(
      [orphan, known],
      sidebarProjectOptions([known], [])
    )

    expect(grouped.projects).toHaveLength(1)
    expect(grouped.recents.map((thread) => thread.id)).toEqual(["orphan"])
  })
})
