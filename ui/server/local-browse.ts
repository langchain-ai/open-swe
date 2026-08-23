import fs from "node:fs"
import os from "node:os"
import path from "node:path"

import {
  createError,
  getRequestHeader,
  getRequestURL,
  type proxyRequest,
} from "h3"

import { isSameOriginRequest } from "./local-graph-proxy"
import { projectsFile } from "./local/project-store"

type Event = Parameters<typeof proxyRequest>[0]

export interface DirectoryListing {
  path: string
  parent: string | null
  entries: Array<{ name: string; path: string; isRepository: boolean }>
}

export function listDirectories(
  requested: string | null,
  home = os.homedir()
): DirectoryListing {
  const target = path.resolve(requested?.trim() || home)
  const parent = path.dirname(target)
  const entries: DirectoryListing["entries"] = []
  for (const entry of fs.readdirSync(target, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith(".")) continue
    const absolute = path.join(target, entry.name)
    entries.push({
      name: entry.name,
      path: absolute,
      isRepository: fs.existsSync(path.join(absolute, ".git")),
    })
  }
  entries.sort((left, right) => left.name.localeCompare(right.name))
  return { path: target, parent: parent === target ? null : parent, entries }
}

/**
 * Directory listing for the project picker. A browser cannot open a native file
 * dialog, and the paths that matter are the server's, not the viewer's — so the
 * server enumerates them and the UI renders the chooser.
 */
export default async function localBrowse(event: Event) {
  if (
    !isSameOriginRequest(
      getRequestHeader(event, "origin"),
      getRequestHeader(event, "sec-fetch-site"),
      getRequestURL(event).origin
    )
  ) {
    throw createError({ statusCode: 403, statusMessage: "Forbidden" })
  }
  if (!projectsFile()) {
    throw createError({
      statusCode: 503,
      statusMessage: "This server has no local project store",
    })
  }

  try {
    return listDirectories(getRequestURL(event).searchParams.get("path"))
  } catch {
    throw createError({
      statusCode: 400,
      statusMessage: "Could not read that directory",
    })
  }
}
