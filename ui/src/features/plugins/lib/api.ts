import type {
  AttachmentType,
  Commit,
  CursorWire,
  JsonObject,
  PluginPermission,
  PullRequest,
  PullRequestRole,
  PullRequestSearch,
  RepositoryInfo,
  RepositoryTree,
  StoredAttachment,
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
  | "images"

export interface PluginManifest {
  name: string
  displayName: string
  version: string
  description: string
  sidebar: { label: string; icon: SidebarIcon }
  permissions: Array<PluginPermission>
  /** What reviews do for the app in workspaces that add it to them. */
  review?: ReviewStep | null
}

/** An app's step in pull request reviews: instructions, what it writes, and a browser. */
export interface ReviewStep {
  instructions: string
  writes: Array<string>
  browser: boolean
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
  /** Whether an admin added the app for everyone, so the viewer can't remove it. */
  featured?: boolean
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

function ownerAndName(repository: string): string {
  const [owner = "", name = "", ...rest] = repository.split("/")
  if (rest.length) throw new UnsafePathError("repository must be owner/repo")
  return `${segment(owner)}/${segment(name)}`
}

function repo(id: string, repository: string): string {
  return `${plugin(id)}/github/${ownerAndName(repository)}`
}

/** Bytes the backend serves, `null` for the statuses that mean there are none. */
async function blobOrNull(
  path: string,
  missing: ReadonlyArray<number>
): Promise<Blob | null> {
  const requestId = newRequestId()
  const res = await fetch(dashboardApiUrl(path), {
    credentials: "include",
    headers: {
      [REQUEST_ID_HEADER]: requestId,
      ...dashboardForwardedHeaders(),
    },
  }).catch((cause: unknown) => {
    throw networkError(cause, requestId)
  })
  if (missing.includes(res.status)) return null
  if (!res.ok)
    throw new DashboardRequestError(res.status, res.statusText, requestId)
  return res.blob()
}

function document(id: string, collection: string, key: string): string {
  return `${plugin(id)}/documents/${segment(collection)}/${segments(key)}`
}

function attachment(
  id: string,
  collection: string,
  key: string,
  name: string,
  repository?: string
): string {
  return `${plugin(id)}/attachments/${segment(collection)}/${segment(name)}${query({ key, repository })}`
}

function roomPath(id: string, target: RoomTarget, action: string): string {
  return `${plugin(id)}/rooms/${segments(target.room)}/${action}`
}

interface DocumentWire {
  key: string
  document: JsonObject
  updated_at: string | null
}

interface PullRequestWire {
  number: number
  title: string
  url: string
  state: string
  merged: boolean
  draft: boolean
  head: string
  base: string
  head_sha: string
  base_sha: string
  author: string | null
  created_at: string | null
  updated_at: string | null
  requested_reviewers: Array<string>
  requested_teams: Array<string>
}

const pullRequest = (wire: PullRequestWire): PullRequest => ({
  number: wire.number,
  title: wire.title,
  url: wire.url,
  state: wire.state,
  merged: wire.merged,
  draft: wire.draft,
  head: wire.head,
  base: wire.base,
  headSha: wire.head_sha,
  baseSha: wire.base_sha,
  author: wire.author,
  createdAt: wire.created_at,
  updatedAt: wire.updated_at,
  requestedReviewers: wire.requested_reviewers,
  requestedTeams: wire.requested_teams,
})

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

/**
 * The `plugin-documents` invalidation key the backend sends when `target`
 * changes (`DocumentScope.topic_key`): the plugin and collection folded into
 * a fixed-length digest, so the key fits a topic with any repository's name.
 */
export async function documentsTopicKey(
  pluginId: string,
  target: DocumentTarget,
  userId: string
): Promise<string> {
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(`${pluginId}/${target.collection}`)
  )
  const digest = Array.from(new Uint8Array(hash, 0, 16), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("")
  return `${digest}/${target.repository ?? `user/${userId}`}`
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
  /** A release the viewer may add, of their own app or a listed one. */
  appVersion: (id: string) =>
    agentsRequest<AppVersion>(
      `/plugins/apps/versions/${encodeURIComponent(id)}`
    ),
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
    body: { repository?: string; document: JsonObject },
    { ifAbsent = false }: { ifAbsent?: boolean } = {}
  ) =>
    stored(
      await agentsRequest<DocumentWire>(
        ifAbsent
          ? `${document(id, collection, key)}?if_absent=true`
          : document(id, collection, key),
        { method: "PUT", ...json(body) }
      )
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
  attach: async (
    id: string,
    target: DocumentTarget,
    key: string,
    name: string,
    bytes: Uint8Array,
    contentType: AttachmentType
  ): Promise<StoredAttachment> => {
    const wire = await agentsRequest<{
      name: string
      content_type: AttachmentType
      size: number
      updated_at: string | null
    }>(
      attachment(
        id,
        target.collection,
        key,
        name,
        target.repository ?? undefined
      ),
      {
        method: "PUT",
        headers: { "Content-Type": contentType },
        body: new Uint8Array(bytes),
      }
    )
    return {
      name: wire.name,
      contentType: wire.content_type,
      size: wire.size,
      updatedAt: wire.updated_at,
    }
  },
  /** A document's attachment as bytes, `null` when absent. */
  attachment: (id: string, target: DocumentTarget, key: string, name: string) =>
    blobOrNull(
      attachment(
        id,
        target.collection,
        key,
        name,
        target.repository ?? undefined
      ),
      [404]
    ),
  detach: (id: string, target: DocumentTarget, key: string, name: string) =>
    agentsRequest<void>(
      attachment(
        id,
        target.collection,
        key,
        name,
        target.repository ?? undefined
      ),
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
  image: (id: string, repository: string, path: string, ref: string) =>
    blobOrNull(
      `${repo(id, repository)}/image${query({ path, ref })}`,
      [404, 415]
    ),
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
  pull: async (id: string, repository: string, number: number) => {
    const found = await orNull(
      agentsRequest<PullRequestWire>(`${repo(id, repository)}/pulls/${number}`)
    )
    return found && pullRequest(found)
  },
  findPull: async (id: string, repository: string, head: string) => {
    const found = await agentsRequest<PullRequestWire | null>(
      `${repo(id, repository)}/pulls${query({ head })}`
    )
    return found && pullRequest(found)
  },
  searchPulls: async (
    id: string,
    role: PullRequestRole
  ): Promise<PullRequestSearch> => {
    const found = await agentsRequest<{
      pulls: Array<{
        repository: string
        number: number
        title: string
        url: string
        draft: boolean
        author: string | null
        created_at: string | null
        updated_at: string | null
      }>
      incomplete: boolean
    }>(`${plugin(id)}/github/pulls${query({ role })}`)
    return {
      pulls: found.pulls.map((pull) => ({
        repository: pull.repository,
        number: pull.number,
        title: pull.title,
        url: pull.url,
        draft: pull.draft,
        author: pull.author,
        createdAt: pull.created_at,
        updatedAt: pull.updated_at,
      })),
      incomplete: found.incomplete,
    }
  },
  openPull: async (
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
    pullRequest(
      await agentsRequest<PullRequestWire>(`${repo(id, repository)}/pulls`, {
        method: "POST",
        ...json(body),
      })
    ),

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
  /**
   * Why the backend refuses a stream of `rooms`, or `null` once it starts
   * streaming, which is dropped at once.
   */
  roomsStreamError: async (
    id: string,
    rooms: Array<RoomTarget & { after: number }>
  ): Promise<AgentsApiError | null> => {
    const requestId = newRequestId()
    const stream = new AbortController()
    const res = await fetch(dashboardApiUrl(roomsStreamUrl(id, rooms)), {
      credentials: "include",
      signal: stream.signal,
      headers: {
        [REQUEST_ID_HEADER]: requestId,
        ...dashboardForwardedHeaders(),
      },
    }).catch((cause: unknown) => {
      throw networkError(cause, requestId)
    })
    if (res.ok) {
      stream.abort()
      return null
    }
    return AgentsApiError.fromResponse(res, requestId)
  },
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
