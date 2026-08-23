import { createError, getRequestURL, readBody, type proxyRequest } from "h3"

import { requireLocalMode } from "./local/guard"
import {
  addProject,
  projectsFile,
  readProjects,
  removeProject,
} from "./local/project-store"

type Event = Parameters<typeof proxyRequest>[0]

/**
 * The project list a local run can work in. Served here rather than over
 * Electron IPC so the desktop app and a plain `open-swe` server behave the
 * same — the browser is the client in both cases.
 */
export default async function localProjects(event: Event) {
  requireLocalMode(event)

  const file = projectsFile()
  if (!file) {
    throw createError({
      statusCode: 503,
      statusMessage: "This server has no local project store",
    })
  }

  const method = event.req.method
  if (method === "GET") return readProjects(file)

  if (method === "POST") {
    const body = (await readBody(event)) as { cwd?: unknown }
    if (typeof body?.cwd !== "string" || !body.cwd) {
      throw createError({ statusCode: 400, statusMessage: "cwd is required" })
    }
    try {
      return addProject(file, body.cwd)
    } catch (error) {
      throw createError({
        statusCode: 400,
        statusMessage:
          error instanceof Error ? error.message : "Could not add the project",
      })
    }
  }

  if (method === "DELETE") {
    const cwd = getRequestURL(event).searchParams.get("cwd")
    if (!cwd) {
      throw createError({ statusCode: 400, statusMessage: "cwd is required" })
    }
    return { removed: removeProject(file, cwd) }
  }

  throw createError({ statusCode: 405, statusMessage: "Method not allowed" })
}
