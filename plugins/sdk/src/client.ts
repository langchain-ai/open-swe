import {
  CONNECT_MESSAGE,
  HELLO_MESSAGE,
  NONCE_META,
  PROTOCOL_VERSION,
  type DocumentScope,
  type HostContext,
  type HostEvent,
  type HostMessage,
  type HostMethod,
  type JsonDocument,
  type JsonObject,
  type ParamsOf,
  type Person,
  type PluginMessage,
  type ResultOf,
  type RoomScope,
  type StoredDocument,
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

type Listener = (event: HostEvent) => void

/** The dashboard, as a plugin sees it. Create one with `connect()`. */
export class PluginHost {
  private nextId = 1
  private readonly pending = new Map<
    number,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >()
  private readonly listeners = new Set<Listener>()
  private readonly watches = new DocumentWatches(this)
  private readonly profiles = new Map<string, Person>()

  private constructor(
    private readonly port: MessagePort,
    public context: HostContext
  ) {
    port.onmessage = (message: MessageEvent<HostMessage>) =>
      this.receive(message.data)
  }

  /** Asks the dashboard for this frame's port, then waits for it and the context. */
  static connect(): Promise<PluginHost> {
    return new Promise((resolve) => {
      const onMessage = (message: MessageEvent) => {
        if (
          message.source !== window.parent ||
          message.data?.type !== CONNECT_MESSAGE ||
          !(message.ports[0] instanceof MessagePort)
        )
          return
        window.removeEventListener("message", onMessage)
        const port = message.ports[0]
        port.onmessage = (first: MessageEvent<HostMessage>) => {
          const data = first.data
          if (data.kind === "event" && data.event.type === "context")
            resolve(new PluginHost(port, data.event.context))
        }
        port.start()
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

  call<M extends HostMethod>(
    method: M,
    params: ParamsOf<M>
  ): Promise<ResultOf<M>> {
    const id = this.nextId++
    const message: PluginMessage = { kind: "request", id, method, params }
    return new Promise((resolve, reject) => {
      this.pending.set(id, {
        resolve: (value) => resolve(value as ResultOf<M>),
        reject,
      })
      this.port.postMessage(message)
    })
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private receive(message: HostMessage): void {
    if (message.kind === "event") {
      if (message.event.type === "context") this.context = message.event.context
      if (message.event.type === "theme")
        this.context = { ...this.context, theme: message.event.theme }
      if (message.event.type === "path")
        this.context = { ...this.context, path: message.event.path }
      for (const listener of this.listeners) listener(message.event)
      return
    }
    const waiting = this.pending.get(message.id)
    if (!waiting) return
    this.pending.delete(message.id)
    if ("error" in message)
      waiting.reject(new HostError(message.error.message, message.error.status))
    else waiting.resolve(message.result)
  }

  readonly nav = {
    setPath: (path: string, options: { replace?: boolean } = {}) =>
      this.call("nav.setPath", { path, ...options }),
    openThread: (threadId: string) => this.call("nav.openThread", { threadId }),
    openExternal: (url: string) => this.call("nav.openExternal", { url }),
  }

  /** The repositories the person shared with the plugin, the only ones it reaches. */
  readonly repositories = {
    list: () => this.call("repositories.list", {}),
    /** Asks the person to choose repositories to share; `[]` when they cancel. */
    pick: (options: ParamsOf<"repositories.pick"> = {}) =>
      this.call("repositories.pick", options),
  }

  /** Profiles (name, picture) for GitHub logins, cached for this frame's life. */
  readonly people = {
    get: async (logins: string[]): Promise<Person[]> => {
      const missing = [...new Set(logins)].filter(
        (login) => !this.profiles.has(login.toLowerCase())
      )
      for (let i = 0; i < missing.length; i += 100) {
        const found = await this.call("people.get", {
          logins: missing.slice(i, i + 100),
        })
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
    toast: (params: ParamsOf<"ui.toast">) => this.call("ui.toast", params),
  }

  readonly storage = {
    /**
     * One collection of JSON documents, in one scope: private to the person,
     * or shared per repository. `K` types its keys (any `string` subtype) and
     * `V` its documents.
     */
    collection: <K extends string, V extends JsonDocument<V>>(
      name: string,
      scope: DocumentScope = {}
    ): Collection<K, V> =>
      new Collection<K, V>(this, this.watches, name, scope),
  }

  /** One repository's GitHub calls, made with the person's own access. */
  github(repository: string) {
    return {
      info: () => this.call("github.repository", { repository }),
      ref: (name: string) => this.call("github.ref", { repository, name }),
      tree: (sha: string) => this.call("github.tree", { repository, sha }),
      file: (path: string, ref: string) =>
        this.call("github.file", { repository, path, ref }),
      history: (path: string, ref: string, limit?: number) =>
        this.call("github.history", { repository, path, ref, limit }),
      createBranch: (name: string, sha: string) =>
        this.call("github.createBranch", { repository, name, sha }),
      commit: (
        params: Omit<ParamsOf<"github.commit">, "repository">
      ): Promise<string> =>
        this.call("github.commit", { repository, ...params }),
      findPull: (head: string) =>
        this.call("github.findPull", { repository, head }),
      openPull: (params: Omit<ParamsOf<"github.openPull">, "repository">) =>
        this.call("github.openPull", { repository, ...params }),
    }
  }

  readonly threads = {
    start: (params: ParamsOf<"threads.start">) =>
      this.call("threads.start", params),
    list: () => this.call("threads.list", {}),
  }

  /** Joins a live room; leave it with the returned room's `leave()`. */
  joinRoom(scope: RoomScope, handlers: RoomHandlers, after = 0): LiveRoom {
    return new LiveRoom(this, scope, handlers, after)
  }
}

/** Shares one `storage.watch` per collection and scope among everyone listening. */
class DocumentWatches {
  private readonly counts = new Map<string, number>()

  constructor(private readonly host: PluginHost) {}

  add(collection: string, scope: DocumentScope, onChange: () => void) {
    const repository = scope.repository?.toLowerCase() ?? null
    const id = JSON.stringify([collection, repository])
    const count = this.counts.get(id) ?? 0
    this.counts.set(id, count + 1)
    if (count === 0)
      void this.host.call("storage.watch", { ...scope, collection })
    const off = this.host.on((event) => {
      if (
        event.type === "storage.changed" &&
        event.collection === collection &&
        event.repository === repository
      )
        onChange()
    })
    return () => {
      off()
      const left = (this.counts.get(id) ?? 1) - 1
      if (left > 0) this.counts.set(id, left)
      else {
        this.counts.delete(id)
        void this.host.call("storage.unwatch", { ...scope, collection })
      }
    }
  }
}

/** A named collection of documents in one scope; see `host.storage.collection`. */
export class Collection<K extends string, V extends JsonDocument<V>> {
  constructor(
    private readonly host: PluginHost,
    private readonly watches: DocumentWatches,
    readonly name: string,
    readonly scope: DocumentScope
  ) {}

  async get(key: K): Promise<StoredDocument<V, K> | null> {
    const found = await this.host.call("storage.get", {
      ...this.scope,
      collection: this.name,
      key,
    })
    return found as StoredDocument<V, K> | null
  }

  /** Documents whose fields include every field of `where`, oldest first; at most `limit` (500). */
  async query(
    options: { where?: Partial<V>; limit?: number } = {}
  ): Promise<StoredDocument<V, K>[]> {
    const found = await this.host.call("storage.query", {
      ...this.scope,
      collection: this.name,
      where: options.where as JsonObject | undefined,
      limit: options.limit,
    })
    return found as StoredDocument<V, K>[]
  }

  async put(key: K, document: V): Promise<StoredDocument<V, K>> {
    const stored = await this.host.call("storage.put", {
      ...this.scope,
      collection: this.name,
      key,
      document: document as JsonObject,
    })
    return stored as StoredDocument<V, K>
  }

  async delete(key: K): Promise<void> {
    await this.host.call("storage.delete", {
      ...this.scope,
      collection: this.name,
      key,
    })
  }

  /** Calls `onChange` whenever anyone writes a document here; returns the unsubscribe. */
  watch(onChange: () => void): () => void {
    return this.watches.add(this.name, this.scope, onChange)
  }
}

export interface RoomHandlers {
  onUpdate: (update: Uint8Array, seq: number) => void
  onSynced?: (seq: number) => void
  onPresence?: (client: string, presence: Uint8Array) => void
}

/** One joined room: its incoming updates and presence, and what this member sends. */
export class LiveRoom {
  private readonly off: () => void
  private readonly repository: string | null
  /** The highest sequence number seen, for compaction and rejoining. */
  seq: number

  constructor(
    private readonly host: PluginHost,
    readonly scope: RoomScope,
    handlers: RoomHandlers,
    after: number
  ) {
    this.seq = after
    this.repository = scope.repository?.toLowerCase() ?? null
    this.off = host.on((event) => {
      if (!("room" in event) || event.room !== scope.room) return
      if (event.repository !== this.repository) return
      if (event.type === "room.update") {
        this.seq = Math.max(this.seq, event.seq)
        handlers.onUpdate(event.update, event.seq)
      } else if (event.type === "room.synced") {
        this.seq = Math.max(this.seq, event.seq)
        handlers.onSynced?.(event.seq)
      } else if (event.type === "room.presence") {
        handlers.onPresence?.(event.client, event.presence)
      }
    })
    void host.call("rooms.join", { ...scope, after })
  }

  publish(update: Uint8Array): Promise<number> {
    return this.host.call("rooms.publish", { ...this.scope, update })
  }

  compact(snapshot: Uint8Array, through: number): Promise<number> {
    return this.host.call("rooms.compact", { ...this.scope, snapshot, through })
  }

  presence(client: string, presence: Uint8Array): Promise<null> {
    return this.host.call("rooms.presence", { ...this.scope, client, presence })
  }

  leave(): void {
    this.off()
    void this.host.call("rooms.leave", this.scope)
  }
}

export function connect(): Promise<PluginHost> {
  return PluginHost.connect()
}
