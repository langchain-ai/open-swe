import type {
  Commit,
  CursorWire,
  JsonObject,
  PluginPermission,
  PullRequest,
  RepositoryInfo,
  RepositoryTree,
  StoredDocument,
} from "@openswe/plugin-sdk"

import { AgentsApiError, agentsRequest } from "@/features/agents/lib/api"
import {
  DashboardRequestError,
  REQUEST_ID_HEADER,
  dashboardApiUrl,
  dashboardForwardedHeaders,
  networkError,
  newRequestId,
} from "@/lib/dashboard-fetch"
import type { AgentToolOffer } from "@/features/plugins/lib/bridge"

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
  sidebar: { label: string; icon: SidebarIcon }
  permissions: Array<PluginPermission>
}

/** One immutable build of an app. */
export interface AppVersion {
  id: string
  number: number
  manifest: PluginManifest
  /** Where it was built (repository, commit, thread), as its publisher said. */
  origin: Record<string, string>
  preview: boolean
  created_at: string | null
}

/** An app the viewer may add: their own, or one someone listed. */
export interface App {
  id: string
  name: string
  mine: boolean
  listed: boolean
  /** Its newest release; none while it has only previews. */
  latest: AppVersion | null
}

export interface InstalledPlugin {
  id: string
  /** The app's id, shared by every install of it; keys its documents' topics. */
  plugin_id: string
  name: string
  manifest: PluginManifest
  version: AppVersion
  /** Whether the viewer publishes the app. */
  mine: boolean
  /** The repositories the viewer shared with it. */
  repositories: Array<string>
  installed_at: string | null
}

/** The app an agent builds in a thread, as it runs beside it. */
export interface AppPreview {
  running: InstalledPlugin | null
  /** A newer build asking for permissions the viewer has not approved. */
  waiting: AppVersion | null
}

export interface InstallRequest {
  version: string
  permissions: Array<PluginPermission>
}

/** The permissions a version asks for beyond the approved ones, from a 409. */
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

export class UnsafePathError extends Error {}

/**
 * One path segment from a plugin's input. The browser resolves `.` and `..`
 * before sending a request, so a segment like that would carry the call to
 * some other dashboard route with the person's cookies; refuse it outright.
 */
function segment(value: string): string {
  if (!value || value === "." || value === ".." || /[%\\]/.test(value))
    throw new UnsafePathError(`unsafe path segment ${JSON.stringify(value)}`)
  return encodeURIComponent(value)
}

/** A plugin's slash-separated name (a key, a room), each part one safe segment. */
function segments(value: string): string {
  return value.split("/").map(segment).join("/")
}

function plugin(id: string): string {
  return `/plugins/${segment(id)}`
}

function repo(id: string, repository: string): string {
  const [owner = "", name = "", ...rest] = repository.split("/")
  if (rest.length) throw new UnsafePathError("repository must be owner/repo")
  return `${plugin(id)}/github/${segment(owner)}/${segment(name)}`
}

function document(id: string, collection: string, key: string): string {
  return `${plugin(id)}/documents/${segment(collection)}/${segments(key)}`
}

function roomPath(id: string, target: RoomTarget, action: string): string {
  return `${plugin(id)}/rooms/${segments(target.room)}/${action}`
}

interface DocumentWire {
  key: string
  document: JsonObject
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

function base64(bytes: Uint8Array): string {
  let binary = ""
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

export function fromBase64(base64Text: string): Uint8Array {
  const binary = atob(base64Text)
  const out = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i)
  return out
}

const avatars = new Map<string, Promise<Blob | null>>()

/** A picture as bytes the sandboxed frame can show, fetched once per URL. */
export function avatarBlob(url: string | null): Promise<Blob | null> {
  if (!url) return Promise.resolve(null)
  const sized = `${url}${url.includes("?") ? "&" : "?"}s=96`
  let pending = avatars.get(sized)
  if (!pending) {
    pending = fetch(sized, { credentials: "omit" })
      .then((res) => (res.ok ? res.blob() : null))
      .catch(() => null)
    avatars.set(sized, pending)
  }
  return pending
}

export interface RoomTarget {
  room: string
  repository?: string
}

/** One collection's documents in one scope: a repository's, or the person's own (`null`). */
export interface DocumentTarget {
  collection: string
  repository: string | null
}

/** The `plugin-documents` invalidation key the backend sends when `target` changes. */
export function documentsTopicKey(
  pluginId: string,
  target: DocumentTarget,
  userId: string
): string {
  const scope = target.repository ?? `user/${userId}`
  return `${pluginId}/${target.collection}/${scope}`
}

/** One stream for every room a frame has joined, each from just after `after`. */
export function roomsStreamUrl(
  id: string,
  rooms: Array<RoomTarget & { after: number }>
) {
  return `${plugin(id)}/rooms/stream${query({
    rooms: JSON.stringify(
      rooms.map(({ room, repository, after }) => ({
        room,
        repository: repository ?? null,
        after,
      }))
    ),
  })}`
}

export const pluginsApi = {
  list: () => agentsRequest<Array<InstalledPlugin>>("/plugins"),
  apps: () => agentsRequest<Array<App>>("/plugins/apps"),
  publish: (html: string) =>
    agentsRequest<AppVersion>("/plugins/apps", {
      method: "POST",
      headers: { "Content-Type": "text/html" },
      body: html,
    }),
  setListed: (appId: string, listed: boolean) =>
    agentsRequest<void>(`/plugins/apps/${segment(appId)}`, {
      method: "PATCH",
      ...json({ listed }),
    }),
  install: (body: InstallRequest) =>
    agentsRequest<InstalledPlugin>("/plugins", {
      method: "POST",
      ...json(body),
    }),
  upgrade: (id: string, body: InstallRequest) =>
    agentsRequest<InstalledPlugin>(`${plugin(id)}/upgrade`, {
      method: "POST",
      ...json(body),
    }),
  preview: (threadId: string) =>
    agentsRequest<AppPreview>(`/plugins/previews/${segment(threadId)}`),
  releasePreview: (threadId: string) =>
    agentsRequest<InstalledPlugin>(
      `/plugins/previews/${segment(threadId)}/release`,
      { method: "POST" }
    ),
  approvePreview: (threadId: string, body: InstallRequest) =>
    agentsRequest<AppPreview>(`/plugins/previews/${segment(threadId)}`, {
      method: "POST",
      ...json(body),
    }),
  share: (id: string, repositories: Array<string>) =>
    agentsRequest<InstalledPlugin>(`${plugin(id)}/repositories`, {
      method: "POST",
      ...json({ repositories }),
    }),
  unshare: (id: string, repository: string) =>
    agentsRequest<InstalledPlugin>(
      `${plugin(id)}/repositories/${segments(repository)}`,
      { method: "DELETE" }
    ),
  uninstall: (id: string) =>
    agentsRequest<void>(plugin(id), { method: "DELETE" }),
  bundle: async (id: string) =>
    (await agentsRequest<{ html: string }>(`${plugin(id)}/bundle`)).html,

  queryDocuments: async (
    id: string,
    collection: string,
    body: {
      repository?: string
      where?: JsonObject
      order_by?: string
      direction?: "asc" | "desc"
      prefix?: string
      start?: CursorWire
      end?: CursorWire
      limit?: number
    }
  ) =>
    (
      await agentsRequest<Array<DocumentWire>>(
        `${plugin(id)}/documents/${segment(collection)}/query`,
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
    body: { repository?: string; document: JsonObject }
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
      await agentsRequest<{ sha: string | null }>(
        `${repo(id, repository)}/ref${query({ name })}`
      )
    ).sha,
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
  /** An image in the repository as bytes, `null` when absent or not an image. */
  image: async (
    id: string,
    repository: string,
    path: string,
    ref: string
  ): Promise<Blob | null> => {
    const requestId = newRequestId()
    const res = await fetch(
      dashboardApiUrl(`${repo(id, repository)}/image${query({ path, ref })}`),
      {
        credentials: "include",
        headers: {
          [REQUEST_ID_HEADER]: requestId,
          ...dashboardForwardedHeaders(),
        },
      }
    ).catch((cause: unknown) => {
      throw networkError(cause, requestId)
    })
    if (res.status === 404 || res.status === 415) return null
    if (!res.ok)
      throw new DashboardRequestError(res.status, res.statusText, requestId)
    return res.blob()
  },
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
  deleteBranch: (id: string, repository: string, name: string) =>
    agentsRequest<void>(
      `${repo(id, repository)}/branches/${name.split("/").map(segment).join("/")}`,
      { method: "DELETE" }
    ),
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

  people: async (id: string, logins: Array<string>) => {
    const people = await agentsRequest<
      Array<{ login: string; name: string; avatar_url: string | null }>
    >(
      `${plugin(id)}/people?${logins.map((l) => `login=${encodeURIComponent(l)}`).join("&")}`
    )
    return Promise.all(
      people.map(async (person) => ({
        login: person.login,
        name: person.name,
        avatar: await avatarBlob(person.avatar_url),
      }))
    )
  },

  publishRoom: async (id: string, target: RoomTarget, update: Uint8Array) =>
    (
      await agentsRequest<{ seq: number }>(roomPath(id, target, "updates"), {
        method: "POST",
        ...json({ repository: target.repository, update: base64(update) }),
      })
    ).seq,
  compactRoom: async (
    id: string,
    target: RoomTarget,
    snapshot: Uint8Array,
    through: number
  ) =>
    (
      await agentsRequest<{ seq: number }>(roomPath(id, target, "compact"), {
        method: "POST",
        ...json({
          repository: target.repository,
          snapshot: base64(snapshot),
          through,
        }),
      })
    ).seq,
  announceInRoom: (
    id: string,
    target: RoomTarget,
    client: string,
    presence: Uint8Array
  ) =>
    agentsRequest<void>(roomPath(id, target, "presence"), {
      method: "POST",
      ...json({
        repository: target.repository,
        client,
        presence: base64(presence),
      }),
    }),

  openChat: (id: string, chat: { name: string; repository: string | null }) =>
    agentsRequest<{ chat_id: string; thread_id: string }>(
      `${plugin(id)}/chats`,
      { method: "POST", ...json(chat) }
    ),
  offerTools: (id: string, chatId: string, offer: AgentToolOffer) =>
    agentsRequest<void>(`${plugin(id)}/chats/${segment(chatId)}/tools`, {
      method: "PUT",
      ...json(offer),
    }),
  chatCalls: (id: string, chatId: string) =>
    agentsRequest<Array<ChatCall>>(
      `${plugin(id)}/chats/${segment(chatId)}/calls`
    ),
  claimCalls: (id: string, chatId: string) =>
    agentsRequest<Array<ChatCall>>(
      `${plugin(id)}/chats/${segment(chatId)}/calls/claim`,
      { method: "POST" }
    ),
  answerCalls: (
    id: string,
    chatId: string,
    outputs: Array<{ call_id: string; output: string }>
  ) =>
    agentsRequest<{ run_id: string | null; paused: boolean }>(
      `${plugin(id)}/chats/${segment(chatId)}/outputs`,
      { method: "POST", ...json({ outputs }) }
    ),
}

/** A call the chat's agent made to one of the plugin's tools, waiting for the plugin's frame. */
export interface ChatCall {
  call_id: string
  tool: string
  arguments: JsonObject
}
