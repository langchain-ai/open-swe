/**
 * What an Open SWE plugin and the web UI share: the handshake, and the typed
 * capabilities the web UI hands the plugin over Cap'n Web.
 *
 * The plugin runs in a sandboxed frame whose policy blocks its requests. The
 * web UI hands it one end of a `MessageChannel` and serves `HostApi` on it.
 * Every scoped thing (a collection, a repository, a live room) is its own
 * capability, checked when it is minted and again by the backend on every
 * call, and released when the plugin disposes it or the frame goes away.
 *
 * Cap'n Web drops object keys named like `Object.prototype` members
 * (`constructor`, `toString`, `__proto__`, ...) from what it carries, so
 * anything keyed by the plugin's own data crosses as JSON text or as a list.
 */
import type { RpcTarget } from "capnweb"

export const PROTOCOL_VERSION = 2
/** The `window.postMessage` type that carries the port into the frame. */
export const CONNECT_MESSAGE = "openswe-plugin-connect"
/**
 * What the frame sends its parent to ask for the port, echoing the nonce the
 * web UI put in its document; the web UI answers only the document it made,
 * and only once.
 */
export const HELLO_MESSAGE = "openswe-plugin-hello"
/** The `<meta name>` holding that nonce. */
export const NONCE_META = "openswe-plugin-nonce"
/**
 * Where the web UI's copy of Cap'n Web waits in the frame. The web UI runs it
 * ahead of the plugin's own code, so both ends always speak the same version;
 * plugins never bundle their own.
 */
export const RUNTIME_GLOBAL = "__opensweCapnweb"

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

type JsonShape<T> = T extends string | number | boolean | null | undefined
  ? T
  : T extends (...args: never[]) => unknown
    ? never
    : T extends readonly (infer Item)[]
      ? readonly JsonShape<Item>[]
      : T extends object
        ? { [K in keyof T]: JsonShape<T[K]> }
        : never

/**
 * `T` when it is a JSON object all the way down. Unlike `JsonObject` it takes
 * plain interfaces, with no index signature: `V extends JsonDocument<V>`.
 */
export type JsonDocument<T> = { [K in keyof T]: JsonShape<T[K]> }

export type PluginPermission =
  | "storage"
  | "github:read"
  | "github:write"
  | "agent"
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

export interface StoredDocument<
  V extends JsonDocument<V> = JsonObject,
  K extends string = string,
> {
  key: K
  document: V
  updatedAt: string | null
}

/**
 * Where a document lives. With `repository` it is shared with everyone who can
 * read that repository; without, it is private to the person using the plugin.
 */
export interface DocumentScope {
  repository?: string
}

/**
 * A repository the person shared with the plugin. `isInBaseOrg` says it
 * belongs to one of the organizations this Open SWE serves, so its name alone
 * is clear enough to show; which organizations those are stays private.
 */
export interface SharedRepository {
  /** `owner/repo`, lowercase. */
  fullName: string
  isInBaseOrg: boolean
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

export interface RoomScope extends DocumentScope {
  room: string
}

export interface Toast {
  title: string
  description?: string
  intent?: "success" | "error" | "info"
}

export interface RepositoryPickOptions {
  /** Preselected, such as one a shared link names. */
  suggested?: string[]
  multiple?: boolean
}

/**
 * Which chat the dock beside the plugin shows: one per person under each
 * `name` the plugin chooses, optionally about a repository the person shared.
 * One per repository, per page or per task is the plugin's call.
 */
export interface ChatScope {
  name: string
  repository?: string
}

/**
 * A tool the plugin's frame runs for the agent in its dock. The agent sees it
 * as `<plugin>__<name>`, with `description` and a JSON Schema for its
 * arguments; `run` answers each call, and what it returns or throws is the
 * agent's result.
 */
export interface AgentTool<A = JsonObject> {
  description: string
  parameters: JsonObject
  run(args: A): Promise<JsonValue> | JsonValue
}

export interface CommitRequest {
  branch: string
  expectedHead: string
  message: string
  files: Record<string, string>
  deletions?: string[]
}

/** `CommitRequest` as it crosses the wire: its files as a list, so every path survives. */
export interface CommitWire extends Omit<CommitRequest, "files"> {
  files: { path: string; content: string }[]
}

/** A stored document as it crosses the wire: its JSON text, so every key survives. */
export interface StoredJson {
  key: string
  json: string
  updatedAt: string | null
}

/** A JSON value a query can order documents by. */
export type JsonScalar = string | number | boolean | null

type Present<T> = Exclude<T, undefined>

/** The dotted paths to the fields of `V` that hold a `JsonScalar`: what a query can order by. */
export type FieldPath<V> = {
  [P in keyof V & string]-?: Present<V[P]> extends JsonScalar
    ? P
    : Exclude<V[P], undefined | null> extends readonly unknown[]
      ? never
      : Exclude<V[P], undefined | null> extends object
        ? `${P}.${FieldPath<Exclude<V[P], undefined | null>>}`
        : never
}[keyof V & string]

/** The type of the field at path `P` in `V`. */
export type FieldValue<
  V,
  P extends string,
> = P extends `${infer Head}.${infer Rest}`
  ? Head extends keyof V
    ? FieldValue<Exclude<V[Head], undefined | null>, Rest>
    : never
  : P extends keyof V
    ? Present<V[P]>
    : never

/**
 * A position in a query's order: a key, or with `orderBy` a value of that
 * field, or a document read earlier, which stands for its value and then its
 * key, so paging past ties loses nothing.
 */
export type QueryBound<
  V extends JsonDocument<V>,
  K extends string,
  P extends FieldPath<V> | undefined,
> =
  | (P extends string ? FieldValue<V, P> & JsonScalar : K)
  | StoredDocument<V, K>

/**
 * What to read from a collection and in what order. Without `orderBy`,
 * documents come by key; keys compare character by character, so `"B"` comes
 * before `"a"`. With it, they come by that field and then by key, ordered like
 * Firebase: missing or `null`, `false`, `true`, numbers, then strings.
 * The bounds follow `direction`: in descending order, `startAt` is the highest.
 * Pass at most one of `startAt` and `startAfter`, and of `endAt` and `endBefore`.
 */
export interface QueryOptions<
  V extends JsonDocument<V>,
  K extends string,
  P extends FieldPath<V> | undefined = undefined,
> {
  /** Only documents that contain every one of these fields, nested objects included. */
  where?: Partial<V>
  orderBy?: P
  direction?: "asc" | "desc"
  /** Only documents whose key starts with this. */
  prefix?: string
  startAt?: QueryBound<V, K, P>
  startAfter?: QueryBound<V, K, P>
  endAt?: QueryBound<V, K, P>
  endBefore?: QueryBound<V, K, P>
  /** 1 to 500; 200 by default. */
  limit?: number
}

/** Where a query's range starts or ends, as it crosses the wire. */
export interface CursorWire {
  /** The key, or with `orderBy` the field's value. */
  value: JsonScalar
  /** With `orderBy`, the key that breaks ties at `value`. */
  key?: string
  inclusive: boolean
}

export interface QueryWire {
  /** JSON text of the fields every document contains. */
  where?: string
  /** A dotted field path; omitted orders by key. */
  orderBy?: string
  direction?: "asc" | "desc"
  prefix?: string
  start?: CursorWire
  end?: CursorWire
  limit?: number
}

export interface PullRequestDraft {
  head: string
  base: string
  title: string
  body?: string
  draft?: boolean
}

/** What the web UI tells the plugin as it happens. */
export type HostEvent =
  | { type: "theme"; theme: Theme }
  | { type: "path"; path: string }

/** What a live room delivers: its log after `after`, `synced`, then each update and presence. */
export type RoomEvent =
  | { type: "update"; seq: number; update: Uint8Array }
  | { type: "synced"; seq: number }
  | { type: "presence"; client: string; presence: Uint8Array }

/** Something the plugin ends by disposing it. */
export interface Subscription extends RpcTarget {}

/** The web UI, as the plugin's frame reaches it. */
export interface HostApi extends RpcTarget {
  context(): Promise<HostContext>
  /** Calls `listener` as the theme or the plugin's path changes. */
  subscribe(listener: (event: HostEvent) => void): Promise<Subscription>
  setPath(path: string, replace: boolean): Promise<void>
  openThread(threadId: string): Promise<void>
  /** Opens a web link in a new tab; outside github.com, only once the person agrees. */
  openExternal(url: string): Promise<void>
  toast(toast: Toast): Promise<void>
  /** Shows `title` beside the plugin's name above its frame and names the browser tab "<title> · Open SWE"; `null` clears it. */
  setTitle(title: string | null): Promise<void>
  /** Profiles for GitHub logins, in the order asked; at most 100 per call. */
  people(logins: string[]): Promise<Person[]>
  /**
   * The GitHub repositories the person shared with the plugin. It reaches no
   * other: a call naming one fails with status 403.
   */
  repositories(): Promise<SharedRepository[]>
  /**
   * Asks the person to share repositories, in a picker the web UI draws from
   * those they can read. Resolves to the ones they chose, or `[]` when they
   * cancel; already-shared suggestions resolve at once, without asking.
   */
  pickRepositories(options: RepositoryPickOptions): Promise<SharedRepository[]>
  collection(name: string, scope: DocumentScope): Promise<CollectionApi>
  /** One repository the person shared, acting with their own GitHub access. */
  github(repository: string): Promise<GitHubRepositoryApi>
  /**
   * Offers the agent in the dock tools, as JSON text of
   * `{ tools: [{ name, description, parameters }], instructions }`, in place
   * of any offered before; disposing the subscription withdraws them. `run`
   * gets a call's tool name and the JSON text of its arguments, and resolves
   * to the result as text.
   */
  provideTools(
    json: string,
    run: (tool: string, argumentsJson: string) => Promise<string>
  ): Promise<Subscription>
  /** Chooses the chat the dock shows, without opening it. */
  chooseChat(scope: ChatScope): Promise<void>
  /**
   * What the person has open, as JSON text of at most 8,000 characters; the
   * agent gets the latest with each message they send from the dock, which
   * shows `label` (at most 120 characters) beside it.
   */
  shareContext(json: string, label: string | null): Promise<void>
  /** Opens the dock on the chosen chat, with `draft` in its composer for the person to send. */
  openChat(draft: string | null): Promise<void>
  /** Joins a live room, scoped like documents; dispose the room to leave it. */
  joinRoom(
    scope: RoomScope,
    listener: (event: RoomEvent) => void,
    after: number
  ): Promise<RoomApi>
}

/** One collection of JSON documents in one scope. */
export interface CollectionApi extends RpcTarget {
  get(key: string): Promise<StoredJson | null>
  /** The documents `query` selects, in its order. */
  query(query: QueryWire): Promise<StoredJson[]>
  /** Stores `json`, the text of a JSON object. */
  put(key: string, json: string): Promise<StoredJson>
  delete(key: string): Promise<void>
  /** Calls `onChange` whenever anyone writes a document here. */
  watch(onChange: () => void): Promise<Subscription>
}

export interface GitHubRepositoryApi extends RpcTarget {
  info(): Promise<RepositoryInfo>
  /** The commit a branch, tag or SHA names; `null` when there is none. */
  ref(name: string): Promise<string | null>
  tree(sha: string): Promise<RepositoryTree>
  file(path: string, ref: string): Promise<string | null>
  /** An image file's bytes, for showing with `URL.createObjectURL`; `null` when absent or not an image. */
  image(path: string, ref: string): Promise<Blob | null>
  history(path: string, ref: string, limit?: number): Promise<Commit[]>
  createBranch(name: string, sha: string): Promise<void>
  /** Deletes a branch, never the default one; GitHub closes an open pull request from it. */
  deleteBranch(name: string): Promise<void>
  /** Fails when `branch` moved past `expectedHead`, so no push is overwritten. */
  commit(request: CommitWire): Promise<string>
  findPull(head: string): Promise<PullRequest | null>
  openPull(draft: PullRequestDraft): Promise<PullRequest>
}

/** One live room this plugin joined. */
export interface RoomApi extends RpcTarget {
  /** Appends an update to the room's log; resolves to its sequence number. */
  publish(update: Uint8Array): Promise<number>
  /** Replaces the log through `through` with `snapshot`, which must cover it. */
  compact(snapshot: Uint8Array, through: number): Promise<number>
  /** Broadcasts this member's presence (cursor, selection); nothing is stored. */
  presence(client: string, presence: Uint8Array): Promise<void>
}
