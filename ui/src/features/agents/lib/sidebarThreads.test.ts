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

const project: DesktopProject = {
  cwd: "/Users/example/open-swe",
  name: "open-swe",
  addedAt: 1,
}

describe("sidebar thread adapters", () => {
  it("uses short repository names and namespaced identities", () => {
    const cloud = cloudSidebarThread(cloudThread())
    const local = localSidebarThread(localThread(), project, undefined)

    expect(cloud).toMatchObject({
      key: "cloud:same-id",
      projectKey: "project:langchain-ai/open-swe",
      projectLabel: "open-swe",
    })
    expect(local).toMatchObject({
      key: "local:same-id",
      projectKey: "project:/users/example/open-swe",
      projectLabel: "open-swe",
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
      cloudThread({ id: "a", repo: "api", repoFullName: "acme/api" })
    )
    const other = cloudSidebarThread(
      cloudThread({ id: "b", repo: "api", repoFullName: "other/api" })
    )

    expect(acme.projectKey).not.toBe(other.projectKey)
    expect(sidebarProjectOptions([acme, other], [])).toHaveLength(2)
    // The label is ambiguous, so a local "api" must not be folded into either.
    const local = localSidebarThread(
      localThread({ cwd: "/Users/example/api" }),
      { cwd: "/Users/example/api", name: "api", addedAt: 1 },
      undefined
    )
    const aliases = cloudProjectKeysByLabel([acme, other])
    expect(applyProjectKeyAliases([local], aliases)[0]?.projectKey).toBe(
      local.projectKey
    )
  })
})

describe("groupSidebarThreadsByProject", () => {
  it("buckets threads per project and ranks projects by their freshest thread", () => {
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

  it("sends threads with no known project to Recents", () => {
    const orphan = cloudSidebarThread(
      cloudThread({ id: "orphan", repo: "", repoFullName: "" })
    )
    const known = cloudSidebarThread(
      cloudThread({ id: "known", repo: "alpha", repoFullName: "acme/alpha" })
    )

    const grouped = groupSidebarThreadsByProject(
      [orphan, known],
      sidebarProjectOptions([known], [])
    )

    expect(grouped.projects).toHaveLength(1)
    expect(grouped.recents.map((thread) => thread.id)).toEqual(["orphan"])
  })
})
