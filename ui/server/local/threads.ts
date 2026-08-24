import { graphRequest } from "./graph"
import { projectsFile, readProjects } from "./project-store"
import {
  captureCheckpoint,
  checkpointRef,
  currentBranch,
  deleteRefs,
  readBranchDiff,
  readDiff,
  repoRoot,
  repositoryMetadata,
} from "./git-diff"
import { threadStore } from "./thread-store"
import type { Diff, RepositoryMetadata } from "./git-diff"
import type { LocalThread } from "./thread-store"

export interface LocalDiff extends Diff {
  repository?: RepositoryMetadata
}

const NO_DIFF: LocalDiff = { status: "missing", files: [], truncated: false }
const THREAD_STATUS = { busy: "running", error: "error" } as const

/** Only a directory the user added as a project may be an agent's cwd. */
export function registeredProject(cwd: unknown): string | null {
  if (typeof cwd !== "string" || !cwd) return null
  const file = projectsFile()
  if (!file) return null
  return readProjects(file).some((project) => project.cwd === cwd) ? cwd : null
}

/**
 * Snapshot the worktree so the Changes panel has a base to diff against. A
 * directory that is not a git repository simply has no checkpoint.
 */
async function recordCheckpoint(thread: LocalThread) {
  const repo = await repoRoot(thread.cwd)
  if (!repo) return null
  const ref = checkpointRef(thread.id)
  await captureCheckpoint(repo, ref)
  return threadStore().setCheckpoint(thread.id, {
    repo,
    ref,
    branch: await currentBranch(repo),
  })
}

export async function createLocalThread(
  input: Record<string, unknown>
): Promise<LocalThread> {
  const cwd = registeredProject(input.cwd)
  if (!cwd) {
    throw new Response("Add a valid project before starting a local agent", {
      status: 400,
    })
  }
  const store = threadStore()
  let thread = store.create({ ...input, cwd })
  try {
    thread = (await recordCheckpoint(thread)) ?? thread
    const response = await graphRequest("/threads", {
      method: "POST",
      body: JSON.stringify({
        thread_id: thread.id,
        if_exists: "do_nothing",
        metadata: { graph_id: "agent" },
      }),
    })
    if (!response.ok) {
      throw new Error(`Could not create the graph thread (${response.status})`)
    }
  } catch (error) {
    store.delete(thread.id)
    if (thread.checkpoint.repo && thread.checkpoint.ref) {
      deleteRefs(thread.checkpoint.repo, [thread.checkpoint.ref])
    }
    throw new Response(
      error instanceof Error ? error.message : "Could not start the run",
      { status: 502 }
    )
  }
  return thread
}

/** Which threads the graph currently considers running or failed. */
export async function threadActivity(): Promise<
  Record<string, "running" | "error">
> {
  const response = await graphRequest("/threads/search", {
    method: "POST",
    body: JSON.stringify({ limit: 1_000 }),
  })
  if (!response.ok) return {}
  const threads: unknown = await response.json()
  if (!Array.isArray(threads)) return {}
  const activity: Record<string, "running" | "error"> = {}
  for (const thread of threads) {
    if (!thread || typeof thread !== "object") continue
    const value = thread as {
      thread_id?: unknown
      status?: keyof typeof THREAD_STATUS
    }
    const status = value.status ? THREAD_STATUS[value.status] : undefined
    if (status && typeof value.thread_id === "string") {
      activity[value.thread_id] = status
    }
  }
  return activity
}

/** A running thread owns the checkout, so its branch can still be changing. */
async function refreshedThread(id: string) {
  const store = threadStore()
  const thread = store.get(id)
  if (!thread?.checkpoint.repo) return thread
  const activity = await threadActivity()
  if (activity[id] !== "running") return thread
  const branch = await currentBranch(thread.checkpoint.repo)
  if (!branch || branch === thread.checkpoint.branch) return thread
  return store.setCheckpoint(thread.id, { ...thread.checkpoint, branch })
}

export async function checkpointDiff(id: string): Promise<LocalDiff> {
  const thread = await refreshedThread(id)
  if (!thread?.checkpoint.repo || !thread.checkpoint.ref) return NO_DIFF
  try {
    const [diff, repository] = await Promise.all([
      readDiff(thread.checkpoint.repo, thread.checkpoint.ref),
      repositoryMetadata(
        thread.checkpoint.repo,
        undefined,
        thread.checkpoint.branch
      ),
    ])
    return { ...diff, repository }
  } catch {
    return { status: "error", files: [], truncated: false }
  }
}

export async function branchDiff(id: string): Promise<LocalDiff> {
  const thread = await refreshedThread(id)
  if (!thread?.checkpoint.repo) return NO_DIFF
  try {
    const repository = await repositoryMetadata(
      thread.checkpoint.repo,
      undefined,
      thread.checkpoint.branch
    )
    if (!repository.pr) return { ...NO_DIFF, repository }
    const diff = await readBranchDiff(
      thread.checkpoint.repo,
      repository.pr.baseRef,
      thread.checkpoint.branch
    )
    return { ...diff, repository }
  } catch {
    return { status: "error", files: [], truncated: false }
  }
}
