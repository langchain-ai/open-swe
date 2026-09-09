import type { Client } from "@langchain/langgraph-sdk"
import {
  StreamController,
  StreamStore,
  type RootSnapshot,
  type StreamSubmitOptions,
} from "@langchain/langgraph-sdk/stream"
import { ThreadTranscript } from "./threadTranscript"
import { threadStreamFetch } from "./threadStreamFetch"
import type { QueuedThreadMessage } from "./types"

type RuntimeSnapshot = RootSnapshot & {
  connection: "connecting" | "connected" | "recovering"
  queuedMessages: QueuedThreadMessage[]
}

type Options = {
  client: Client<Record<string, unknown>>
  threadId: string | null
  transport: "cloud" | "local"
  fetch: typeof fetch
  onThreadId: (id: string) => void
  onCreated: () => void
  onCompleted: () => void
}

export class AgentThreadRuntime {
  readonly controller: StreamController
  readonly store: StreamStore<RuntimeSnapshot>
  private transcript = new ThreadTranscript()
  private busy = false
  private generation = 0
  private readVersion = 0
  private disposed = false
  private refreshing?: Promise<void>
  private timer?: ReturnType<typeof setTimeout>
  private connection: RuntimeSnapshot["connection"] = "connecting"
  private disconnectedAt = 0
  private restarting = false
  private unsubscribe: () => void
  private release: () => void
  private accepted?: {
    resolve: () => void
    reject: (error: unknown) => void
    didAccept: boolean
  }
  private queuedMessages: QueuedThreadMessage[] = []
  private error: unknown
  private syncError: unknown

  constructor(private options: Options) {
    const getState = options.client.threads.getState.bind(
      options.client.threads
    )
    options.client.threads.getState = async <
      ValuesType = Record<string, unknown>,
    >(
      ...args: Parameters<typeof getState>
    ) => {
      const generation = this.generation
      const version = ++this.readVersion
      const state = await getState<ValuesType>(...args)
      if (args[1] != null) return state
      // Local state.next describes graph work, not whether a server run exists.
      const thread =
        options.transport === "local"
          ? await options.client.threads.get(args[0])
          : null
      if (
        this.disposed ||
        generation !== this.generation ||
        version !== this.readVersion
      )
        return state
      const status =
        thread?.status ??
        (state as unknown as { thread_status?: string }).thread_status
      const busy =
        status != null
          ? status === "busy" || status === "running" || status === "pending"
          : !Array.isArray(state.next) || state.next.length > 0
      this.busy = busy
      if (!busy) this.connection = "connected"
      const messages = (state.values as Record<string, unknown>)?.messages
      if (Array.isArray(messages))
        this.transcript.checkpoint(
          messages,
          state.metadata?.step as number | undefined,
          !busy && !this.accepted
        )
      if (status === "error")
        this.error ??= new Error(
          "The agent run failed. You can send a follow up to retry."
        )
      if (this.store) this.publish()
      // Make the SDK attach to a run started elsewhere even before its first checkpoint.
      const result = { ...state }
      if (busy) Reflect.deleteProperty(result, "next")
      return result
    }
    this.controller = new StreamController({
      assistantId: "agent",
      client: options.client,
      threadId: options.threadId,
      fetch: threadStreamFetch(options.fetch, (connected) => {
        const previous = this.connection
        this.connection = connected ? "connected" : "recovering"
        if (connected) this.disconnectedAt = 0
        else if (!this.disconnectedAt) this.disconnectedAt = Date.now()
        if (this.store && previous !== this.connection) this.publish()
        if (!connected) void this.refresh()
      }),
      onThreadId: options.onThreadId,
      onCreated: () => {
        this.generation += 1
        this.busy = true
        if (this.accepted) {
          this.accepted.didAccept = true
          this.accepted.resolve()
        }
        this.accepted = undefined
        options.onCreated()
        this.publish()
      },
      onCompleted: () => {
        options.onCompleted()
        void this.refresh()
      },
    })
    this.store = new StreamStore<RuntimeSnapshot>({
      ...this.controller.rootStore.getSnapshot(),
      connection: this.connection,
      queuedMessages: [],
    })
    this.unsubscribe = this.controller.rootStore.subscribe(() => {
      if (!this.restarting)
        this.transcript.update(this.controller.rootStore.getSnapshot().messages)
      this.publish()
    })
    this.release = this.controller.activate()
    void this.controller.hydrationPromise
      .catch(() => undefined)
      .finally(() => this.schedule())
    if (typeof window !== "undefined") {
      window.addEventListener("online", this.wake)
      window.addEventListener("focus", this.wake)
    }
  }

  private publish(): void {
    if (this.disposed) return
    const root = this.controller.rootStore.getSnapshot()
    this.store.setValue({
      ...root,
      messages: this.transcript.messages,
      values: { ...root.values, messages: this.transcript.messages },
      isLoading: this.busy || Boolean(this.accepted),
      isThreadLoading: root.isThreadLoading && !this.transcript.messages.length,
      error: this.error ?? this.syncError ?? root.error,
      connection: this.connection,
      queuedMessages: this.queuedMessages,
    })
  }

  private wake = () => {
    void this.refresh()
  }

  private schedule(): void {
    if (this.disposed) return
    clearTimeout(this.timer)
    this.timer = setTimeout(
      () => void this.refresh(),
      this.busy || this.connection === "recovering" ? 3_000 : 10_000
    )
  }

  refresh = (): Promise<void> => {
    if (this.disposed) return Promise.resolve()
    if (this.refreshing) return this.refreshing
    this.refreshing = this.refreshState()
      .catch((error: unknown) => {
        this.connection = "recovering"
        this.syncError = error
        this.publish()
      })
      .finally(() => {
        this.refreshing = undefined
        this.schedule()
      })
    return this.refreshing
  }

  private async refreshState(): Promise<void> {
    const id = this.controller.rootStore.getSnapshot().threadId
    if (!id || this.accepted || this.restarting) return
    const generation = this.generation
    const wasBusy = this.busy
    await this.options.client.threads.getState(id)
    if (this.disposed || this.accepted || generation !== this.generation) return
    this.syncError = undefined
    if (this.options.transport === "local") {
      const runs = await this.options.client.runs.list(id, {
        status: "pending",
        limit: 100,
      })
      this.queuedMessages = runs.flatMap((run) => {
        const queued = run.metadata?.dashboard_queued_message as
          | QueuedThreadMessage
          | undefined
        return queued ? [queued] : []
      })
    }
    if (this.disposed || this.accepted || generation !== this.generation) return
    // Hydrate alone doesn't restart the SDK's deferred or exhausted pump.
    const reconnect =
      (this.busy && !wasBusy) ||
      (this.disconnectedAt > 0 && Date.now() - this.disconnectedAt > 15_000) ||
      (!this.busy && this.controller.rootStore.getSnapshot().isLoading) ||
      Boolean(this.controller.rootStore.getSnapshot().error)
    if (reconnect) {
      this.restarting = true
      try {
        await this.controller.disconnect()
        await this.controller.hydrate(null)
        await this.controller.hydrate(id)
        this.disconnectedAt = 0
      } finally {
        this.restarting = false
      }
    }
    this.publish()
  }

  /** Resolves at server acceptance, never at run completion. */
  submit = async (
    input: unknown,
    options?: StreamSubmitOptions
  ): Promise<void> => {
    const ids: string[] = []
    if (
      input &&
      typeof input === "object" &&
      "messages" in input &&
      Array.isArray(input.messages)
    ) {
      input = {
        ...input,
        messages: input.messages.map((message: Record<string, unknown>) => {
          const id =
            typeof message.id === "string" ? message.id : crypto.randomUUID()
          ids.push(id)
          return { ...message, id }
        }),
      }
    }
    return this.acceptCommand(() => this.controller.submit(input, options), ids)
  }

  respond = (...args: Parameters<StreamController["respond"]>): Promise<void> =>
    this.acceptCommand(() => this.controller.respond(...args))

  respondAll = (
    ...args: Parameters<StreamController["respondAll"]>
  ): Promise<void> =>
    this.acceptCommand(() => this.controller.respondAll(...args))

  private async acceptCommand(
    dispatch: () => Promise<void>,
    ids: string[] = []
  ): Promise<void> {
    if (this.accepted || this.restarting)
      throw new Error(
        "The thread is reconnecting or sending. Please retry shortly."
      )
    this.generation += 1
    this.error = undefined
    const pending = {
      resolve: () => {},
      reject: (_error: unknown) => {},
      didAccept: false,
    }
    const accepted = new Promise<void>((resolve, reject) => {
      pending.resolve = resolve
      pending.reject = reject
    })
    this.accepted = pending
    const generation = this.generation
    this.publish()
    void dispatch().then(
      () => {
        // A disconnect before acceptance must not silently discard the draft.
        if (this.accepted === pending) {
          pending?.reject(
            new Error("The message was not accepted. Please retry.")
          )
          this.transcript.reject(ids)
          this.accepted = undefined
        }
        void this.refresh()
      },
      (error: unknown) => {
        if (!pending?.didAccept) this.transcript.reject(ids)
        if (this.accepted === pending) {
          pending?.reject(error)
          this.accepted = undefined
        }
        if (this.generation <= generation + 1) {
          this.error = error
          this.busy = false
        }
        this.publish()
        void this.refresh()
      }
    )
    return accepted
  }

  /** Native server queue survives navigation, reload and a finishing-run race. */
  enqueue = async (
    input: Record<string, unknown>,
    options: StreamSubmitOptions,
    message: QueuedThreadMessage
  ): Promise<void> => {
    const id = this.store.getSnapshot().threadId
    if (!id) throw new Error("Thread is not ready")
    this.generation += 1
    if (Array.isArray(input.messages)) {
      input = {
        ...input,
        messages: input.messages.map(
          (entry: Record<string, unknown>, index: number) => ({
            ...entry,
            id: entry.id ?? `${message.id}:${index}`,
          })
        ),
      }
    }
    await this.options.client.runs.create(id, "agent", {
      input,
      config: {
        ...options.config,
        configurable: {
          ...options.config?.configurable,
          __event_streaming_v2: true,
        },
      },
      durability: "sync",
      streamResumable: true,
      streamSubgraphs: true,
      streamMode: [
        "values",
        "updates",
        "messages",
        "custom",
        "tasks",
        "checkpoints",
      ],
      metadata: { ...options.metadata, dashboard_queued_message: message },
      multitaskStrategy: "enqueue",
    })
    this.generation += 1
    this.busy = true
    this.queuedMessages = [...this.queuedMessages, message]
    this.publish()
    void this.refresh()
  }

  stop = async (options?: { cancel?: boolean }): Promise<void> => {
    const id = this.store.getSnapshot().threadId
    if (id && this.options.transport === "local" && options?.cancel !== false) {
      // Reattached controllers don't know the active run id; ask the server.
      const runs = await this.options.client.runs.list(id, { limit: 100 })
      const active = runs.filter(
        (run) => run.status === "pending" || run.status === "running"
      )
      // Cancel queued work first so cancelling the current run cannot start it.
      active.sort(
        (a, b) =>
          Number(a.status === "running") - Number(b.status === "running")
      )
      for (const run of active)
        await this.options.client.runs.cancel(id, run.run_id)
      await this.controller.disconnect()
    } else await this.controller.stop(options)
    await this.refresh()
  }

  dispose(): void {
    this.disposed = true
    clearTimeout(this.timer)
    this.unsubscribe()
    this.release()
    if (typeof window !== "undefined") {
      window.removeEventListener("online", this.wake)
      window.removeEventListener("focus", this.wake)
    }
  }
}
