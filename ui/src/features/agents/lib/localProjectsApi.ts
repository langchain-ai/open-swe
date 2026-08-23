import type { DesktopProject } from "@/desktop"

export interface DirectoryEntry {
  name: string
  path: string
  isRepository: boolean
}

export interface DirectoryListing {
  path: string
  parent: string | null
  entries: Array<DirectoryEntry>
}

async function local<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(detail || `Request failed (${response.status})`)
  }
  return (await response.json()) as T
}

/**
 * The local project list, served over HTTP so the desktop app and a plain
 * `open-swe` server share one implementation.
 */
export const localProjectsApi = {
  list: () => local<Array<DesktopProject>>("/local/projects"),
  add: (cwd: string) =>
    local<DesktopProject>("/local/projects", {
      method: "POST",
      body: JSON.stringify({ cwd }),
    }),
  remove: (cwd: string) =>
    local<{ removed: boolean }>(
      `/local/projects?cwd=${encodeURIComponent(cwd)}`,
      { method: "DELETE" }
    ),
  browse: (path?: string) =>
    local<DirectoryListing>(
      path ? `/local/browse?path=${encodeURIComponent(path)}` : "/local/browse"
    ),
}
