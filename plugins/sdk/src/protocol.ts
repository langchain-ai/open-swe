/**
 * The messages an Open SWE plugin and the dashboard exchange.
 *
 * The plugin runs in a sandboxed frame with no network access. The dashboard
 * hands it one end of a `MessageChannel`; every request goes over that port,
 * and the dashboard answers it by calling the backend for the person using the
 * plugin, within the permissions they granted.
 */

export const PROTOCOL_VERSION = 1
/** The `window.postMessage` type that carries the port into the frame. */
export const CONNECT_MESSAGE = "openswe-plugin-connect"

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

export type PluginPermission =
  | "storage"
  | "github:read"
  | "github:write"
  | "threads"

export type Theme = "light" | "dark"

export interface HostContext {
  protocol: number
  plugin: {
    id: string
    name: string
    displayName: string
    version: string
    permissions: PluginPermission[]
    /** The GitHub repositories (`owner/repo`, lowercase) the plugin may reach. */
    repositories: string[]
  }
  user: { login: string; avatarUrl: string | null }
  theme: Theme
  /** The plugin's own path after its dashboard URL, starting with `/`. */
  path: string
}

export interface StoredDocument<T extends JsonValue = JsonValue> {
  key: string
  document: T
  updatedAt: string | null
}

/**
 * Where a document lives. With `repository` it is shared with everyone who can
 * read that repository; without, it is private to the person using the plugin.
 */
export interface DocumentScope {
  repository?: string
}

export interface RepositoryInfo {
  fullName: string
  defaultBranch: string
  private: boolean
}

export interface TreeEntry {
  path: string
  type: "blob" | "tree" | "commit"
  size: number | null
}

export interface RepositoryTree {
  sha: string
  truncated: boolean
  entries: TreeEntry[]
}

export interface Commit {
  sha: string
  message: string
  authorName: string
  authorLogin: string | null
  authoredAt: string | null
}

export interface PullRequest {
  number: number
  title: string
  url: string
  state: string
  draft: boolean
  head: string
  base: string
}

export interface AgentThread {
  threadId: string
  title: string
  status: string
  updatedAtMs: number | null
}

/** Every request a plugin may make: its params and what it resolves to. */
export interface HostMethods {
  "nav.setPath": { params: { path: string; replace?: boolean }; result: null }
  "nav.openThread": { params: { threadId: string }; result: null }
  /** Opens a github.com link in a new tab. */
  "nav.openExternal": { params: { url: string }; result: null }
  "ui.toast": {
    params: {
      title: string
      description?: string
      intent?: "success" | "error" | "info"
    }
    result: null
  }

  "storage.query": {
    params: DocumentScope & {
      collection: string
      /** Every field listed must match the document's. */
      where?: JsonObject
      limit?: number
    }
    result: StoredDocument[]
  }
  "storage.get": {
    params: DocumentScope & { collection: string; key: string }
    result: StoredDocument | null
  }
  "storage.put": {
    params: DocumentScope & {
      collection: string
      key: string
      document: JsonValue
    }
    result: StoredDocument
  }
  "storage.delete": {
    params: DocumentScope & { collection: string; key: string }
    result: null
  }
  /** Sends `storage.changed` whenever someone writes a document in this scope. */
  "storage.watch": { params: DocumentScope; result: null }
  "storage.unwatch": { params: DocumentScope; result: null }

  "github.repository": {
    params: { repository: string }
    result: RepositoryInfo
  }
  /** The commit a branch, tag or SHA names; `null` when there is none. */
  "github.ref": {
    params: { repository: string; name: string }
    result: string | null
  }
  "github.tree": {
    params: { repository: string; sha: string }
    result: RepositoryTree
  }
  "github.file": {
    params: { repository: string; path: string; ref: string }
    result: string | null
  }
  "github.history": {
    params: { repository: string; path: string; ref: string; limit?: number }
    result: Commit[]
  }
  "github.createBranch": {
    params: { repository: string; name: string; sha: string }
    result: null
  }
  /** Fails when `branch` moved past `expectedHead`, so no push is overwritten. */
  "github.commit": {
    params: {
      repository: string
      branch: string
      expectedHead: string
      message: string
      files: Record<string, string>
      deletions?: string[]
    }
    result: string
  }
  "github.findPull": {
    params: { repository: string; head: string }
    result: PullRequest | null
  }
  "github.openPull": {
    params: {
      repository: string
      head: string
      base: string
      title: string
      body?: string
      draft?: boolean
    }
    result: PullRequest
  }

  /**
   * Asks the person to approve starting an agent thread with `request`; `null`
   * when they decline. The dashboard shows them the request word for word.
   */
  "threads.start": {
    params: { repository: string; title: string; request: string }
    result: { threadId: string } | null
  }
  "threads.list": { params: Record<string, never>; result: AgentThread[] }
}

export type HostMethod = keyof HostMethods
export type ParamsOf<M extends HostMethod> = HostMethods[M]["params"]
export type ResultOf<M extends HostMethod> = HostMethods[M]["result"]

export type HostEvent =
  | { type: "context"; context: HostContext }
  | { type: "theme"; theme: Theme }
  | { type: "path"; path: string }
  | { type: "storage.changed"; repository: string | null }

export type PluginMessage = {
  kind: "request"
  id: number
  method: HostMethod
  params: unknown
}

export type HostMessage =
  | { kind: "response"; id: number; result: unknown }
  | {
      kind: "response"
      id: number
      error: { message: string; status: number | null }
    }
  | { kind: "event"; event: HostEvent }
