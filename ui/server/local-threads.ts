import { createError, getRequestURL, readBody, type proxyRequest } from "h3"

import { requireLocalMode } from "./local/guard"
import { localGraphConfiguration } from "./local-graph-proxy"
import { readProjects, projectsFile } from "./local/project-store"
import { threadStore } from "./local/thread-store"

type Event = Parameters<typeof proxyRequest>[0]

/** Only a directory the user added as a project may be an agent's cwd. */
function registeredProject(cwd: unknown): string | null {
  if (typeof cwd !== "string" || !cwd) return null
  const file = projectsFile()
  if (!file) return null
  return readProjects(file).some((project) => project.cwd === cwd) ? cwd : null
}

async function createGraphThread(threadId: string): Promise<void> {
  const { origin, token } = localGraphConfiguration()
  const response = await fetch(`${origin}/threads`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      thread_id: threadId,
      if_exists: "do_nothing",
      metadata: { graph_id: "agent" },
    }),
  })
  if (!response.ok) {
    throw new Error(`Could not create the graph thread (${response.status})`)
  }
}

const THREAD_STATUS = { busy: "running", error: "error" } as const

/** Which threads the graph currently considers running or failed. */
async function threadActivity(): Promise<Record<string, "running" | "error">> {
  const { origin, token } = localGraphConfiguration()
  const response = await fetch(`${origin}/threads/search`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
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

/**
 * Local threads, served over HTTP so the desktop app and a plain `open-swe`
 * server share one implementation and one store.
 */
export default async function localThreads(event: Event) {
  requireLocalMode(event)

  const store = threadStore()
  const url = getRequestURL(event)
  const [id, section] = url.pathname
    .replace(/^\/local\/threads\/?/, "")
    .split("/")
  const method = event.req.method

  if (id === "activity") return threadActivity()

  if (!id) {
    if (method === "GET") return store.list()
    if (method === "POST") {
      const body = (await readBody(event)) as { cwd?: unknown }
      const cwd = registeredProject(body?.cwd)
      if (!cwd) {
        throw createError({
          statusCode: 400,
          statusMessage: "Add a valid project before starting a local agent",
        })
      }
      const thread = store.create({ ...(body as object), cwd })
      try {
        await createGraphThread(thread.id)
      } catch (error) {
        store.delete(thread.id)
        throw createError({
          statusCode: 502,
          statusMessage:
            error instanceof Error ? error.message : "Could not start the run",
        })
      }
      return thread
    }
    throw createError({ statusCode: 405, statusMessage: "Method not allowed" })
  }

  // The prompt a thread was created with, replayed once the stream is ready.
  if (section === "prompt") {
    if (method === "GET") return store.pendingPrompt(id)
    if (method === "DELETE") return store.clearPrompt(id)
    throw createError({ statusCode: 405, statusMessage: "Method not allowed" })
  }

  if (method === "GET") {
    const thread = store.get(id)
    if (!thread)
      throw createError({ statusCode: 404, statusMessage: "Not found" })
    return thread
  }

  if (method === "PATCH") {
    const patch = await readBody(event)
    try {
      const thread = store.update(id, patch)
      if (!thread)
        throw createError({ statusCode: 404, statusMessage: "Not found" })
      return thread
    } catch (error) {
      if (error && typeof error === "object" && "statusCode" in error)
        throw error
      throw createError({
        statusCode: 400,
        statusMessage:
          error instanceof Error ? error.message : "Invalid thread update",
      })
    }
  }

  if (method === "DELETE") {
    return { deleted: Boolean(store.delete(id)) }
  }

  throw createError({ statusCode: 405, statusMessage: "Method not allowed" })
}
