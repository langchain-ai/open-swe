import type { RpcStub } from "capnweb"

import {
  CONNECT_MESSAGE,
  HELLO_MESSAGE,
  NONCE_META,
  PROTOCOL_VERSION,
  RUNTIME_GLOBAL,
  type AgentTool,
  type ChatScope,
  type CollectionApi,
  type CommitRequest,
  type CursorWire,
  type DocumentScope,
  type FieldPath,
  type GitHubRepositoryApi,
  type HostApi,
  type HostContext,
  type HostEvent,
  type JsonDocument,
  type JsonObject,
  type JsonScalar,
  type JsonValue,
  type Person,
  type PullRequestDraft,
  type QueryBound,
  type QueryOptions,
  type RepositoryPickOptions,
  type RoomApi,
  type RoomEvent,
  type RoomScope,
  type StoredDocument,
  type StoredJson,
  type Toast,
} from "./protocol"

export class HostError extends Error {
  constructor(
    message: string,
    readonly status: number | null
  ) {
    super(message)
    this.name = "HostError"
  }
}

/** What the web UI injects into the frame: its own copy of Cap'n Web. */
interface Runtime {
  newMessagePortRpcSession<T extends HostApi>(port: MessagePort): RpcStub<T>
}

function runtime(): Runtime {
  const found = (globalThis as Record<string, unknown>)[RUNTIME_GLOBAL]
  if (!found)
    throw new Error("Open SWE plugins run only inside the Open SWE web UI")
  return found as Runtime
}

/** Settles like `request`, with any failure as a `HostError` carrying its status. */
async function settle<T>(request: PromiseLike<T>): Promise<T> {
  try {
    return await request
  } catch (error) {
    if (error instanceof Error) {
      const status = (error as { status?: unknown }).status
      throw new HostError(
        error.message,
        typeof status === "number" ? status : null
      )
    }
    throw new HostError(String(error), null)
  }
}

function scopeKey(scope: DocumentScope): string {
  return scope.repository?.toLowerCase() ?? ""
}

type Listener = (event: HostEvent) => void

/** The web UI, as a plugin sees it. Create one with `connect()`. */
export class PluginHost {
  private readonly listeners = new Set<Listener>()
  private readonly profiles = new Map<string, Person>()
  private readonly collections = new Map<string, Collection<string, never>>()
  private readonly repositoryClients = new Map<string, GitHubRepository>()

  private constructor(
    private readonly api: RpcStub<HostApi>,
    public context: HostContext
  ) {
    void api.subscribe((event) => this.receive(event))
  }

  /** Asks the web UI for this frame's port, then waits for it and the context. */
  static connect(): Promise<PluginHost> {
    return new Promise((resolve, reject) => {
      const onMessage = (message: MessageEvent) => {
        if (
          message.source !== window.parent ||
          message.data?.type !== CONNECT_MESSAGE ||
          !(message.ports[0] instanceof MessagePort)
        )
          return
        window.removeEventListener("message", onMessage)
        const api = runtime().newMessagePortRpcSession<HostApi>(
          message.ports[0]
        )
        settle(api.context()).then(
          (context) => resolve(new PluginHost(api, context)),
          reject
        )
      }
      window.addEventListener("message", onMessage)
      const nonce = document
        .querySelector(`meta[name="${NONCE_META}"]`)
        ?.getAttribute("content")
      window.parent.postMessage(
        { type: HELLO_MESSAGE, nonce, version: PROTOCOL_VERSION },
        "*"
      )
    })
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private receive(event: HostEvent): void {
    if (event.type === "theme")
      this.context = { ...this.context, theme: event.theme }
    if (event.type === "path")
      this.context = { ...this.context, path: event.path }
    for (const listener of this.listeners) listener(event)
  }

  readonly nav = {
    setPath: (path: string, options: { replace?: boolean } = {}) =>
      settle(this.api.setPath(path, options.replace === true)),
    openThread: (threadId: string) => settle(this.api.openThread(threadId)),
    openExternal: (url: string) => settle(this.api.openExternal(url)),
  }

  /** The repositories the person shared with the plugin, the only ones it reaches. */
  readonly repositories = {
    list: () => settle(this.api.repositories()),
    /** Asks the person to choose repositories to share; `[]` when they cancel. */
    pick: (options: RepositoryPickOptions = {}) =>
      settle(this.api.pickRepositories(options)),
  }

  /** Profiles (name, picture) for GitHub logins, cached for this frame's life. */
  readonly people = {
    get: async (logins: string[]): Promise<Person[]> => {
      const missing = [...new Set(logins)].filter(
        (login) => !this.profiles.has(login.toLowerCase())
      )
      for (let i = 0; i < missing.length; i += 100) {
        const found = await settle(this.api.people(missing.slice(i, i + 100)))
        for (const person of found)
          this.profiles.set(person.login.toLowerCase(), person)
      }
      return logins.flatMap((login) => {
        const person = this.profiles.get(login.toLowerCase())
        return person ? [person] : []
      })
    },
  }

  readonly ui = {
    toast: (toast: Toast) => settle(this.api.toast(toast)),
    /** Shows `title` beside the plugin's name above its frame and names the browser tab "<title> · Open SWE"; `null` clears it. */
    setTitle: (title: string | null) => settle(this.api.setTitle(title)),
  }

  readonly storage = {
    /**
     * One collection of JSON documents, in one scope: private to the person,
     * or shared per repository. `K` types its keys (any `string` subtype) and
     * `V` its documents. The same name and scope give the same collection.
     */
    collection: <K extends string, V extends JsonDocument<V>>(
      name: string,
      scope: DocumentScope = {}
    ): Collection<K, V> => {
      const id = JSON.stringify([name, scopeKey(scope)])
      let found = this.collections.get(id)
      if (!found) {
        found = new Collection<string, never>(
          this.api.collection(name, scope),
          name,
          scope
        )
        this.collections.set(id, found)
      }
      return found as unknown as Collection<K, V>
    },
  }

  /** One repository's GitHub calls, made with the person's own access. */
  github(repository: string): GitHubRepository {
    const id = repository.toLowerCase()
    let found = this.repositoryClients.get(id)
    if (!found) {
      found = new GitHubRepository(this.api.github(repository))
      this.repositoryClients.set(id, found)
    }
    return found
  }

  /** The agent in the dock beside the plugin, which the person chats with. */
  readonly agent = {
    /**
     * Offers the agent `tools`, keyed by name, in place of any offered
     * before, with `instructions` on when to use them; returns the withdrawal.
     */
    provide: (
      tools: Record<string, AgentTool>,
      instructions = ""
    ): (() => void) => {
      const json = JSON.stringify({
        tools: Object.entries(tools).map(([name, tool]) => ({
          name,
          description: tool.description,
          parameters: tool.parameters,
        })),
        instructions,
      })
      const subscription = this.api.provideTools(
        json,
        async (name, argumentsJson) => {
          const tool = Object.hasOwn(tools, name) ? tools[name] : undefined
          if (!tool)
            throw new Error(`${name} is not one of this plugin's tools`)
          const result = await tool.run(JSON.parse(argumentsJson) as JsonObject)
          return typeof result === "string" ? result : JSON.stringify(result)
        }
      )
      subscription.catch((error: unknown) =>
        console.error("Open SWE refused the agent's tools", error)
      )
      return () => subscription[Symbol.dispose]()
    },
    /** Chooses the chat the dock shows, without opening it. */
    use: (scope: ChatScope) => settle(this.api.chooseChat(scope)),
    /**
     * Tells the agent what the person has open: the page, their selection,
     * anything it should know without asking. Each message the person sends
     * from the dock carries the latest, so share again as it changes; the
     * thread shows `label`, such as the page's name, under the message.
     */
    share: (context: JsonValue, options: { label?: string } = {}) =>
      settle(
        this.api.shareContext(JSON.stringify(context), options.label ?? null)
      ),
    /** Opens the dock on the chosen chat, with `draft` in its composer for the person to send. */
    open: (options: { draft?: string } = {}) =>
      settle(this.api.openChat(options.draft ?? null)),
  }

  /** Joins a live room; leave it with the returned room's `leave()`. */
  joinRoom(scope: RoomScope, handlers: RoomHandlers, after = 0): LiveRoom {
    return new LiveRoom(this.api, scope, handlers, after)
  }
}

/**
 * A named collection of documents in one scope; see `host.storage.collection`.
 * Documents arrive as JSON and are trusted to be `V`, the one place the SDK
 * takes the plugin's word for a type.
 */
export class Collection<K extends string, V extends JsonDocument<V>> {
  constructor(
    private readonly stub: RpcStub<CollectionApi>,
    readonly name: string,
    readonly scope: DocumentScope
  ) {}

  async get(key: K): Promise<StoredDocument<V, K> | null> {
    const found = await settle(this.stub.get(key))
    return found && this.#fromWire(found)
  }

  /**
   * Documents in order, by key or by the field `orderBy` names, within the
   * bounds given; see `QueryOptions`. To page, start after the last document
   * of the page before.
   */
  async query<P extends FieldPath<V> | undefined = undefined>(
    options: QueryOptions<V, K, P> = {}
  ): Promise<StoredDocument<V, K>[]> {
    const { where, orderBy, startAt, startAfter, endAt, endBefore } = options
    if (startAt !== undefined && startAfter !== undefined)
      throw new TypeError("pass startAt or startAfter, not both")
    if (endAt !== undefined && endBefore !== undefined)
      throw new TypeError("pass endAt or endBefore, not both")
    const cursor = (
      bound: QueryBound<V, K, P> | undefined,
      inclusive: boolean
    ): CursorWire | undefined => {
      if (bound === undefined) return undefined
      if (typeof bound !== "object" || bound === null)
        return { value: bound, inclusive }
      return orderBy === undefined
        ? { value: bound.key, inclusive }
        : { value: fieldAt(bound.document, orderBy), key: bound.key, inclusive }
    }
    const found = await settle(
      this.stub.query({
        where: where === undefined ? undefined : JSON.stringify(where),
        orderBy,
        direction: options.direction,
        prefix: options.prefix,
        start: cursor(startAt ?? startAfter, startAfter === undefined),
        end: cursor(endAt ?? endBefore, endBefore === undefined),
        limit: options.limit,
      })
    )
    return found.map((stored) => this.#fromWire(stored))
  }

  async put(key: K, document: V): Promise<StoredDocument<V, K>> {
    const stored = await settle(this.stub.put(key, JSON.stringify(document)))
    return this.#fromWire(stored)
  }

  #fromWire(stored: StoredJson): StoredDocument<V, K> {
    return {
      key: stored.key as K,
      document: JSON.parse(stored.json) as V,
      updatedAt: stored.updatedAt,
    }
  }

  delete(key: K): Promise<void> {
    return settle(this.stub.delete(key))
  }

  /** Calls `onChange` whenever anyone writes a document here; returns the unsubscribe. */
  watch(onChange: () => void): () => void {
    const subscription = this.stub.watch(() => onChange())
    return () => subscription[Symbol.dispose]()
  }
}

/** The scalar at a dotted path in a document; `null` when it is missing or not a scalar. */
function fieldAt(document: object, path: string): JsonScalar {
  let value: unknown = document
  for (const name of path.split(".")) {
    if (!value || typeof value !== "object" || !Object.hasOwn(value, name))
      return null
    value = (value as Record<string, unknown>)[name]
  }
  return value === null ||
    ["string", "number", "boolean"].includes(typeof value)
    ? (value as JsonScalar)
    : null
}

/** One repository the person shared, acting with their own GitHub access. */
export class GitHubRepository {
  constructor(private readonly stub: RpcStub<GitHubRepositoryApi>) {}

  info() {
    return settle(this.stub.info())
  }
  ref(name: string) {
    return settle(this.stub.ref(name))
  }
  tree(sha: string) {
    return settle(this.stub.tree(sha))
  }
  file(path: string, ref: string) {
    return settle(this.stub.file(path, ref))
  }
  /** An image in the repository as a Blob, for `URL.createObjectURL`; the frame loads none itself. */
  image(path: string, ref: string) {
    return settle(this.stub.image(path, ref))
  }
  history(path: string, ref: string, limit?: number) {
    return settle(this.stub.history(path, ref, limit))
  }
  createBranch(name: string, sha: string) {
    return settle(this.stub.createBranch(name, sha))
  }
  /** Deletes a branch, never the default one; GitHub closes an open pull request from it. */
  deleteBranch(name: string) {
    return settle(this.stub.deleteBranch(name))
  }
  commit(request: CommitRequest): Promise<string> {
    return settle(
      this.stub.commit({
        ...request,
        files: Object.entries(request.files).map(([path, content]) => ({
          path,
          content,
        })),
      })
    )
  }
  findPull(head: string) {
    return settle(this.stub.findPull(head))
  }
  openPull(draft: PullRequestDraft) {
    return settle(this.stub.openPull(draft))
  }
}

export interface RoomHandlers {
  onUpdate: (update: Uint8Array, seq: number) => void
  onSynced?: (seq: number) => void
  onPresence?: (client: string, presence: Uint8Array) => void
}

/** One joined room: its incoming updates and presence, and what this member sends. */
export class LiveRoom {
  private readonly stub: RpcStub<RoomApi>
  /** The highest sequence number seen, for compaction and rejoining. */
  seq: number

  constructor(
    api: RpcStub<HostApi>,
    readonly scope: RoomScope,
    handlers: RoomHandlers,
    after: number
  ) {
    this.seq = after
    this.stub = api.joinRoom(
      scope,
      (event: RoomEvent) => {
        if (event.type === "update") {
          this.seq = Math.max(this.seq, event.seq)
          handlers.onUpdate(event.update, event.seq)
        } else if (event.type === "synced") {
          this.seq = Math.max(this.seq, event.seq)
          handlers.onSynced?.(event.seq)
        } else {
          handlers.onPresence?.(event.client, event.presence)
        }
      },
      after
    )
  }

  publish(update: Uint8Array): Promise<number> {
    return settle(this.stub.publish(update))
  }

  compact(snapshot: Uint8Array, through: number): Promise<number> {
    return settle(this.stub.compact(snapshot, through))
  }

  presence(client: string, presence: Uint8Array): Promise<void> {
    return settle(this.stub.presence(client, presence))
  }

  leave(): void {
    this.stub[Symbol.dispose]()
  }
}

export function connect(): Promise<PluginHost> {
  return PluginHost.connect()
}
