/**
 * The messages an Open SWE plugin and the dashboard exchange.
 *
 * The plugin runs in a sandboxed frame whose policy blocks its requests. The dashboard
 * hands it one end of a `MessageChannel`; every request goes over that port,
 * and the dashboard answers it by calling the backend for the person using the
 * plugin, within the permissions they granted.
 */

export const PROTOCOL_VERSION = 1
/** The `window.postMessage` type that carries the port into the frame. */
export const CONNECT_MESSAGE = "openswe-plugin-connect"
/**
 * What the frame sends its parent to ask for the port, echoing the nonce the
 * dashboard put in its document; the dashboard answers only the document it
 * made, and only once.
 */
export const HELLO_MESSAGE = "openswe-plugin-hello"
/** The `<meta name>` holding that nonce. */
export const NONCE_META = "openswe-plugin-nonce"

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
  | "realtime"

export type Theme = "light" | "dark"

export interface HostContext {
  protocol: number
  plugin: {
    id: string
    name: string
    displayName: string
    version: string
    permissions: PluginPermission[]
  }
  /** The person using the plugin. */
  user: Person
  theme: Theme
  /** The plugin's own path after its dashboard URL, starting with `/`. */
  path: string
}

/**
 * Someone the plugin can show: their GitHub login, display name and picture.
 * The frame cannot load remote images, so the dashboard fetches the picture
 * and hands it over as a Blob; show it with `URL.createObjectURL`.
 */
export interface Person {
  login: string
  name: string
  avatar: Blob | null
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
  /** Profiles for GitHub logins, in the order asked; at most 100 per call. */
  "people.get": { params: { logins: string[] }; result: Person[] }
  /**
   * The GitHub repositories (`owner/repo`, lowercase) the person shared with
   * the plugin. It reaches no other: a call naming one fails with status 403.
   */
  "repositories.list": { params: Record<string, never>; result: string[] }
  /**
   * Asks the person to share repositories, in a picker the dashboard draws
   * from those they can read. Resolves to the ones they chose, or `[]` when
   * they cancel. `suggested` come preselected, such as one a shared link
   * names; when they are already shared it resolves at once, without asking.
   */
  "repositories.pick": {
    params: { suggested?: string[]; multiple?: boolean }
    result: string[]
  }
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

  /**
   * Starts streaming a live room: every update after `after`, then `room.synced`,
   * then each update and presence as others send them. Scoped like documents.
   */
  "rooms.join": {
    params: RoomScope & { after?: number }
    result: null
  }
  "rooms.leave": { params: RoomScope; result: null }
  /** Appends an update to the room's log; resolves to its sequence number. */
  "rooms.publish": {
    params: RoomScope & { update: Uint8Array }
    result: number
  }
  /** Replaces the log through `through` with `snapshot`, which must cover it. */
  "rooms.compact": {
    params: RoomScope & { snapshot: Uint8Array; through: number }
    result: number
  }
  /** Broadcasts this member's presence (cursor, selection); nothing is stored. */
  "rooms.presence": {
    params: RoomScope & { client: string; presence: Uint8Array }
    result: null
  }
}

export interface RoomScope extends DocumentScope {
  room: string
}

export type HostMethod = keyof HostMethods
export type ParamsOf<M extends HostMethod> = HostMethods[M]["params"]
export type ResultOf<M extends HostMethod> = HostMethods[M]["result"]

export type HostEvent =
  | { type: "context"; context: HostContext }
  | { type: "theme"; theme: Theme }
  | { type: "path"; path: string }
  | { type: "storage.changed"; repository: string | null }
  | {
      type: "room.update"
      room: string
      repository: string | null
      seq: number
      update: Uint8Array
    }
  | {
      type: "room.synced"
      room: string
      repository: string | null
      seq: number
    }
  | {
      type: "room.presence"
      room: string
      repository: string | null
      client: string
      presence: Uint8Array
    }

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
