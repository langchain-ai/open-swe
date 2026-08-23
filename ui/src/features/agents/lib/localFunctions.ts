import { createServerFn } from "@tanstack/react-start"

import { assertLocalRequest } from "../../../../server/local/guard"
import { listDirectories } from "../../../../server/local/browse"
import {
  
  addProject,
  projectsFile,
  readProjects,
  removeProject
} from "../../../../server/local/project-store"
import {
  checkoutBranch,
  currentBranch,
  localBranches,
} from "../../../../server/local/git-diff"
import {
  branchDiff,
  checkpointDiff,
  createLocalThread,
  registeredProject,
  threadActivity,
} from "../../../../server/local/threads"
import { threadStore } from "../../../../server/local/thread-store"
import type {LocalProject} from "../../../../server/local/project-store";

/** The project file is required: without one there is nowhere to record a project. */
function requireProjectsFile(): string {
  const file = projectsFile()
  if (!file) {
    throw new Response("This server has no local project store", {
      status: 503,
    })
  }
  return file
}

function asPath(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new Response("A path is required", { status: 400 })
  }
  return value
}

function asOptionalPath(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}

function asThreadId(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new Response("A thread id is required", { status: 400 })
  }
  return value
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {}
}

export const listLocalProjects = createServerFn().handler(
  (): Array<LocalProject> => {
    assertLocalRequest()
    return readProjects(requireProjectsFile())
  }
)

export const addLocalProject = createServerFn({ method: "POST" })
  .inputValidator(asPath)
  .handler(({ data }): LocalProject => {
    assertLocalRequest()
    try {
      return addProject(requireProjectsFile(), data)
    } catch (error) {
      throw new Response(
        error instanceof Error ? error.message : "Could not add the project",
        { status: 400 }
      )
    }
  })

export const removeLocalProject = createServerFn({ method: "POST" })
  .inputValidator(asPath)
  .handler(({ data }) => {
    assertLocalRequest()
    return { removed: removeProject(requireProjectsFile(), data) }
  })

/**
 * Directory listing for the project picker. A browser cannot open a native file
 * dialog, and the paths that matter are the server's, not the viewer's — so the
 * server enumerates them and the UI renders the chooser.
 */
export const browseLocalDirectories = createServerFn()
  .inputValidator(asOptionalPath)
  .handler(({ data }) => {
    assertLocalRequest()
    try {
      return listDirectories(data)
    } catch {
      throw new Response("Could not read that directory", { status: 400 })
    }
  })

export const listLocalBranches = createServerFn()
  .inputValidator(asPath)
  .handler(async ({ data }) => {
    assertLocalRequest()
    const project = registeredProject(data)
    if (!project) return { current: null, branches: [] as Array<string> }
    const [current, branches] = await Promise.all([
      currentBranch(project),
      localBranches(project),
    ])
    return { current, branches }
  })

export const checkoutLocalBranch = createServerFn({ method: "POST" })
  .inputValidator((value: unknown) => {
    const input = asRecord(value)
    return {
      cwd: asPath(input.cwd),
      branch: asPath(input.branch),
      create: input.create === true,
    }
  })
  .handler(async ({ data }) => {
    assertLocalRequest()
    const project = registeredProject(data.cwd)
    if (!project) {
      throw new Response("Project is not registered", { status: 400 })
    }
    try {
      return { branch: await checkoutBranch(project, data.branch, data.create) }
    } catch (error) {
      throw new Response(
        error instanceof Error ? error.message : "Could not checkout branch",
        { status: 400 }
      )
    }
  })

export const listLocalThreads = createServerFn().handler(() => {
  assertLocalRequest()
  return threadStore().list()
})

export const getLocalThread = createServerFn()
  .inputValidator(asThreadId)
  .handler(({ data }) => {
    assertLocalRequest()
    return threadStore().get(data) ?? null
  })

export const startLocalThread = createServerFn({ method: "POST" })
  .inputValidator(asRecord)
  .handler(({ data }) => {
    assertLocalRequest()
    return createLocalThread(data)
  })

export const updateLocalThread = createServerFn({ method: "POST" })
  .inputValidator((value: unknown) => {
    const input = asRecord(value)
    return { id: asThreadId(input.id), patch: asRecord(input.patch) }
  })
  .handler(({ data }) => {
    assertLocalRequest()
    try {
      const thread = threadStore().update(data.id, data.patch)
      if (!thread) throw new Response("Not found", { status: 404 })
      return thread
    } catch (error) {
      if (error instanceof Response) throw error
      throw new Response(
        error instanceof Error ? error.message : "Invalid thread update",
        { status: 400 }
      )
    }
  })

export const deleteLocalThread = createServerFn({ method: "POST" })
  .inputValidator(asThreadId)
  .handler(({ data }) => {
    assertLocalRequest()
    return { deleted: Boolean(threadStore().delete(data)) }
  })

/** The prompt a thread was created with, replayed once its stream is ready. */
export const localThreadPrompt = createServerFn()
  .inputValidator(asThreadId)
  .handler(({ data }) => {
    assertLocalRequest()
    return threadStore().pendingPrompt(data)
  })

export const clearLocalThreadPrompt = createServerFn({ method: "POST" })
  .inputValidator(asThreadId)
  .handler(({ data }) => {
    assertLocalRequest()
    return threadStore().clearPrompt(data)
  })

export const localThreadActivity = createServerFn().handler(() => {
  assertLocalRequest()
  return threadActivity()
})

export const localThreadDiff = createServerFn()
  .inputValidator(asThreadId)
  .handler(({ data }) => {
    assertLocalRequest()
    return checkpointDiff(data)
  })

export const localThreadBranchDiff = createServerFn()
  .inputValidator(asThreadId)
  .handler(({ data }) => {
    assertLocalRequest()
    return branchDiff(data)
  })
