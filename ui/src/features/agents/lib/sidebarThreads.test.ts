import { describe, expect, it } from "vitest"

import type { DesktopLocalThreadSummary, DesktopProject } from "@/desktop"
import type { AgentThread } from "./types"
import {
  cloudSidebarThread,
  filterSidebarProject,
  localSidebarThread,
  sidebarProjectOptions,
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
      projectKey: "project:open-swe",
      projectLabel: "open-swe",
    })
    expect(local).toMatchObject({
      key: "local:same-id",
      projectKey: "project:open-swe",
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

  it("merges cloud and local projects by repository name", () => {
    const threads = [
      cloudSidebarThread(cloudThread()),
      localSidebarThread(localThread(), project, undefined),
    ]

    expect(sidebarProjectOptions(threads, [project])).toEqual([
      { key: "project:open-swe", label: "open-swe" },
    ])
    expect(filterSidebarProject(threads, "project:open-swe")).toHaveLength(2)
  })

  it("sorts the loaded cloud and local window by last update", () => {
    const cloud = cloudSidebarThread(cloudThread({ updatedAt: 20 }))
    const local = localSidebarThread(
      localThread({ updatedAt: 30 }),
      project,
      undefined
    )

    expect(
      sortSidebarThreads([cloud, local]).map((thread) => thread.key)
    ).toEqual(["local:same-id", "cloud:same-id"])
  })
})
