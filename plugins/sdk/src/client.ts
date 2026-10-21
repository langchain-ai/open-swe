import {
  CONNECT_MESSAGE,
  type DocumentScope,
  type HostContext,
  type HostEvent,
  type HostMessage,
  type HostMethod,
  type JsonObject,
  type JsonValue,
  type ParamsOf,
  type PluginMessage,
  type ResultOf,
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
  private readonly watchers = new Map<string | null, number>()

  private constructor(
    private readonly port: MessagePort,
    public context: HostContext
  ) {
    port.onmessage = (message: MessageEvent<HostMessage>) =>
      this.receive(message.data)
  }

  /** Waits for the dashboard to hand this frame its port and context. */
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

  readonly ui = {
    toast: (params: ParamsOf<"ui.toast">) => this.call("ui.toast", params),
  }

  readonly storage = {
    query: <T extends JsonValue>(
      collection: string,
      options: DocumentScope & { where?: JsonObject; limit?: number } = {}
    ) =>
      this.call("storage.query", { collection, ...options }) as Promise<
        StoredDocument<T>[]
      >,
    get: <T extends JsonValue>(
      collection: string,
      key: string,
      scope: DocumentScope = {}
    ) =>
      this.call("storage.get", {
        collection,
        key,
        ...scope,
      }) as Promise<StoredDocument<T> | null>,
    put: <T extends JsonValue>(
      collection: string,
      key: string,
      document: T,
      scope: DocumentScope = {}
    ) =>
      this.call("storage.put", {
        collection,
        key,
        document,
        ...scope,
      }) as Promise<StoredDocument<T>>,
    delete: (collection: string, key: string, scope: DocumentScope = {}) =>
      this.call("storage.delete", { collection, key, ...scope }),
    /** Calls `onChange` whenever a document in `scope` is written; returns the unsubscribe. */
    watch: (scope: DocumentScope, onChange: () => void): (() => void) => {
      const repository = scope.repository?.toLowerCase() ?? null
      const count = this.watchers.get(repository) ?? 0
      this.watchers.set(repository, count + 1)
      if (count === 0) void this.call("storage.watch", scope)
      const off = this.on((event) => {
        if (event.type === "storage.changed" && event.repository === repository)
          onChange()
      })
      return () => {
        off()
        const left = (this.watchers.get(repository) ?? 1) - 1
        if (left > 0) this.watchers.set(repository, left)
        else {
          this.watchers.delete(repository)
          void this.call("storage.unwatch", scope)
        }
      }
    },
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
}

export function connect(): Promise<PluginHost> {
  return PluginHost.connect()
}
