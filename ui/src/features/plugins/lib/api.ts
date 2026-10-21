import type {
  AgentThread,
  Commit,
  JsonObject,
  JsonValue,
  PluginPermission,
  PullRequest,
  RepositoryInfo,
  RepositoryTree,
  StoredDocument,
} from "@openswe/plugin-sdk"

import { AgentsApiError, agentsRequest } from "@/features/agents/lib/api"

export type SidebarIcon =
  | "puzzle-piece"
  | "notebook"
  | "file-text"
  | "book-open"
  | "kanban"
  | "chart-bar"
  | "chat-circle"
  | "calendar"
  | "lightbulb"

export interface PluginManifest {
  name: string
  displayName: string
  version: string
  description: string
  entry: string
  sidebar: { label: string; icon: SidebarIcon }
  permissions: Array<PluginPermission>
}

export interface PluginSource {
  repository: string
  path: string
  ref: string | null
}

export interface InstalledPlugin {
  id: string
  /** Shared by every install of the same source; keys its documents' topics. */
  plugin_id: string
  name: string
  manifest: PluginManifest
  source: PluginSource
  commit_sha: string
  scope: "user" | "organization"
  repositories: Array<string>
  installed_at: string | null
}

export interface CatalogItem {
  manifest: PluginManifest
  source: PluginSource
}

export interface InstallRequest {
  repository: string
  path: string
  ref: string | null
  scope: "user" | "organization"
  name: string
  permissions: Array<PluginPermission>
  repositories: Array<string>
}

/** The permissions a newer commit asks for beyond the approved ones, from a 409. */
export function unapprovedPermissions(
  error: unknown
): Array<PluginPermission> | null {
  if (!(error instanceof AgentsApiError) || error.status !== 409) return null
  try {
    const detail: unknown = JSON.parse(error.message)
    if (
      detail &&
      typeof detail === "object" &&
      "permissions" in detail &&
      Array.isArray(detail.permissions)
    )
      return detail.permissions as Array<PluginPermission>
  } catch {
    return null
  }
  return null
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) })

function query(params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params))
    if (value !== null && value !== undefined && value !== "")
      search.set(key, String(value))
  const text = search.toString()
  return text ? `?${text}` : ""
}

function plugin(id: string): string {
  return `/plugins/${encodeURIComponent(id)}`
}

function repo(id: string, repository: string): string {
  const [owner = "", name = ""] = repository.split("/")
  return `${plugin(id)}/github/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`
}

function document(id: string, collection: string, key: string): string {
  return `${plugin(id)}/documents/${encodeURIComponent(collection)}/${key
    .split("/")
    .map(encodeURIComponent)
    .join("/")}`
}

interface DocumentWire {
  key: string
  document: JsonValue
  updated_at: string | null
}

const stored = (wire: DocumentWire): StoredDocument => ({
  key: wire.key,
  document: wire.document,
  updatedAt: wire.updated_at,
})

async function orNull<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request
  } catch (error) {
    if (error instanceof AgentsApiError && error.status === 404) return null
    throw error
  }
}

export const pluginsApi = {
  list: () => agentsRequest<Array<InstalledPlugin>>("/plugins"),
  catalog: (source: PluginSource) =>
    agentsRequest<Array<CatalogItem>>(
      `/plugins/catalog${query({ ...source })}`
    ),
  install: (body: InstallRequest) =>
    agentsRequest<InstalledPlugin>("/plugins", {
      method: "POST",
      ...json(body),
    }),
  upgrade: (id: string, permissions: Array<PluginPermission>) =>
    agentsRequest<InstalledPlugin>(`${plugin(id)}/upgrade`, {
      method: "POST",
      ...json({ permissions }),
    }),
  setRepositories: (id: string, repositories: Array<string>) =>
    agentsRequest<InstalledPlugin>(`${plugin(id)}/repositories`, {
      method: "PUT",
      ...json({ repositories }),
    }),
  uninstall: (id: string) =>
    agentsRequest<void>(plugin(id), { method: "DELETE" }),
  bundle: async (id: string) =>
    (await agentsRequest<{ html: string }>(`${plugin(id)}/bundle`)).html,

  queryDocuments: async (
    id: string,
    collection: string,
    body: { repository?: string; where?: JsonObject; limit?: number }
  ) =>
    (
      await agentsRequest<Array<DocumentWire>>(
        `${plugin(id)}/documents/${encodeURIComponent(collection)}/query`,
        { method: "POST", ...json(body) }
      )
    ).map(stored),
  getDocument: async (
    id: string,
    collection: string,
    key: string,
    repository?: string
  ) => {
    const found = await orNull(
      agentsRequest<DocumentWire>(
        `${document(id, collection, key)}${query({ repository })}`
      )
    )
    return found && stored(found)
  },
  putDocument: async (
    id: string,
    collection: string,
    key: string,
    body: { repository?: string; document: JsonValue }
  ) =>
    stored(
      await agentsRequest<DocumentWire>(document(id, collection, key), {
        method: "PUT",
        ...json(body),
      })
    ),
  deleteDocument: (
    id: string,
    collection: string,
    key: string,
    repository?: string
  ) =>
    agentsRequest<void>(
      `${document(id, collection, key)}${query({ repository })}`,
      { method: "DELETE" }
    ),

  repository: async (id: string, repository: string) => {
    const info = await agentsRequest<{
      full_name: string
      default_branch: string
      private: boolean
    }>(repo(id, repository))
    return {
      fullName: info.full_name,
      defaultBranch: info.default_branch,
      private: info.private,
    } satisfies RepositoryInfo
  },
  ref: async (id: string, repository: string, name: string) =>
    (
      await orNull(
        agentsRequest<{ sha: string }>(
          `${repo(id, repository)}/ref${query({ name })}`
        )
      )
    )?.sha ?? null,
  tree: (id: string, repository: string, sha: string) =>
    agentsRequest<RepositoryTree>(
      `${repo(id, repository)}/tree${query({ sha })}`
    ),
  file: async (id: string, repository: string, path: string, ref: string) =>
    (
      await orNull(
        agentsRequest<{ text: string }>(
          `${repo(id, repository)}/file${query({ path, ref })}`
        )
      )
    )?.text ?? null,
  history: async (
    id: string,
    repository: string,
    path: string,
    ref: string,
    limit?: number
  ) =>
    (
      await agentsRequest<
        Array<{
          sha: string
          message: string
          author_name: string
          author_login: string | null
          authored_at: string | null
        }>
      >(`${repo(id, repository)}/history${query({ path, ref, limit })}`)
    ).map((commit): Commit => ({
      sha: commit.sha,
      message: commit.message,
      authorName: commit.author_name,
      authorLogin: commit.author_login,
      authoredAt: commit.authored_at,
    })),
  createBranch: (id: string, repository: string, name: string, sha: string) =>
    agentsRequest<void>(`${repo(id, repository)}/branches`, {
      method: "POST",
      ...json({ name, sha }),
    }),
  commit: async (
    id: string,
    repository: string,
    body: {
      branch: string
      expectedHead: string
      message: string
      files: Record<string, string>
      deletions?: Array<string>
    }
  ) =>
    (
      await agentsRequest<{ sha: string }>(`${repo(id, repository)}/commits`, {
        method: "POST",
        ...json({
          branch: body.branch,
          expected_head: body.expectedHead,
          message: body.message,
          files: body.files,
          deletions: body.deletions ?? [],
        }),
      })
    ).sha,
  findPull: (id: string, repository: string, head: string) =>
    agentsRequest<PullRequest | null>(
      `${repo(id, repository)}/pulls${query({ head })}`
    ),
  openPull: (
    id: string,
    repository: string,
    body: {
      head: string
      base: string
      title: string
      body?: string
      draft?: boolean
    }
  ) =>
    agentsRequest<PullRequest>(`${repo(id, repository)}/pulls`, {
      method: "POST",
      ...json(body),
    }),

  startThread: (
    id: string,
    body: { repository: string; title: string; request: string }
  ) =>
    agentsRequest<{ thread_id: string }>(`${plugin(id)}/threads`, {
      method: "POST",
      ...json(body),
    }),
  threads: async (id: string) =>
    (
      await agentsRequest<
        Array<{
          thread_id: string
          title: string
          status: string
          updated_at_ms: number | null
        }>
      >(`${plugin(id)}/threads`)
    ).map((thread): AgentThread => ({
      threadId: thread.thread_id,
      title: thread.title,
      status: thread.status,
      updatedAtMs: thread.updated_at_ms,
    })),
}
