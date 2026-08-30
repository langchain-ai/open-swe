import {
  HttpAgentServerAdapter,
  matchesSubscription,
  type AgentServerAdapter,
} from "@langchain/langgraph-sdk/client"

type ProtocolEvent = Parameters<typeof matchesSubscription>[0]
type SubscribeParams = Parameters<
  NonNullable<AgentServerAdapter["openEventStream"]>
>[0]

const MAX_EVENTS = 10_000
const MAX_BYTES = 16 * 1024 * 1024

type Subscriber = {
  params: SubscribeParams
  queue: AsyncQueue<ProtocolEvent>
}

type ThreadState = {
  values: unknown
  next?: unknown
  tasks?: unknown
  metadata?: unknown
  checkpoint?: { checkpoint_id?: string } | null
  parent_checkpoint?: { checkpoint_id?: string } | null
}
type SnapshotFrame = { type: "snapshot"; state: ThreadState | null }
type EventFrame = { type: "event"; event: ProtocolEvent }
type ResetFrame = { type: "reset"; reason: string }
type Frame = SnapshotFrame | EventFrame | ResetFrame

class AsyncQueue<T> implements AsyncIterable<T> {
  private values: Array<T> = []
  private waiters: Array<(result: IteratorResult<T>) => void> = []
  private closed = false

  push(value: T) {
    if (this.closed) return
    const waiter = this.waiters.shift()
    if (waiter) waiter({ done: false, value })
    else this.values.push(value)
  }

  close() {
    if (this.closed) return
    this.closed = true
    for (const waiter of this.waiters.splice(0))
      waiter({ done: true, value: undefined })
  }

  [Symbol.asyncIterator](): AsyncIterator<T> {
    return {
      next: () => {
        const value = this.values.shift()
        if (value !== undefined)
          return Promise.resolve({ done: false as const, value })
        if (this.closed)
          return Promise.resolve({ done: true as const, value: undefined })
        return new Promise((resolve) => this.waiters.push(resolve))
      },
      return: () => {
        this.close()
        return Promise.resolve({ done: true as const, value: undefined })
      },
    }
  }
}

export class DashboardThreadAdapter implements AgentServerAdapter {
  threadId = ""
  private readonly delegate: HttpAgentServerAdapter
  private readonly apiUrl: string
  private readonly request: typeof fetch
  private controller?: AbortController
  private snapshot?: ThreadState | null
  private snapshotPromise?: Promise<ThreadState | null>
  private eventsBuffer: Array<ProtocolEvent> = []
  private eventBytes = 0
  private subscribers = new Set<Subscriber>()
  private seen = new Set<string>()
  private freshThread = false

  constructor(apiUrl: string, request: typeof fetch) {
    this.apiUrl = apiUrl.replace(/\/$/, "")
    this.request = request
    this.delegate = new HttpAgentServerAdapter({ apiUrl, fetch: request })
  }

  setThreadId(threadId: string) {
    if (threadId === this.threadId) return
    this.reset()
    this.threadId = threadId
    this.delegate.setThreadId(threadId)
  }

  open() {
    return this.delegate.open()
  }

  async send(command: Parameters<AgentServerAdapter["send"]>[0]) {
    if (
      command.method === "run.start" &&
      !this.snapshotPromise &&
      !this.snapshot
    ) {
      this.freshThread = true
    }
    return await this.delegate.send(command)
  }

  events() {
    return this.delegate.events()
  }

  getState = async <StateType = unknown>() => {
    if (this.freshThread) return null
    if (!this.snapshotPromise) this.snapshotPromise = this.startSnapshotStream()
    return (await this.snapshotPromise) as {
      values: StateType
      next?: unknown
      tasks?: unknown
      metadata?: unknown
      checkpoint?: { checkpoint_id?: string } | null
      parent_checkpoint?: { checkpoint_id?: string } | null
    } | null
  }

  openEventStream(
    params: SubscribeParams
  ): ReturnType<NonNullable<AgentServerAdapter["openEventStream"]>> {
    if (this.freshThread) return this.delegate.openEventStream(params)
    const queue = new AsyncQueue<ProtocolEvent>()
    const subscriber = { params, queue }
    this.subscribers.add(subscriber)
    for (const event of this.eventsBuffer) {
      if (event.type === "event" && matchesSubscription(event, params))
        queue.push(event)
    }
    return {
      events: queue,
      ready: this.snapshotPromise?.then(() => undefined) ?? Promise.resolve(),
      close: () => {
        this.subscribers.delete(subscriber)
        queue.close()
      },
    }
  }

  async close() {
    this.reset()
    await this.delegate.close()
  }

  private async startSnapshotStream() {
    this.controller = new AbortController()
    const response = await this.request(
      `${this.apiUrl}/threads/${encodeURIComponent(this.threadId)}/snapshot-stream`,
      { method: "GET", credentials: "include", signal: this.controller.signal }
    )
    if (!response.ok || !response.body)
      throw new Error(`Thread snapshot stream failed: ${response.status}`)

    const reader = response.body
      .pipeThrough(new TextDecoderStream())
      .getReader()
    let pending = ""
    while (true) {
      const { done, value } = await reader.read()
      if (done)
        throw new Error("Thread snapshot stream ended before the snapshot")
      pending += value
      const newline = pending.indexOf("\n")
      if (newline < 0) continue
      const frame = JSON.parse(pending.slice(0, newline)) as Frame
      pending = pending.slice(newline + 1)
      if (frame.type !== "snapshot")
        throw new Error("Invalid thread snapshot stream")
      this.snapshot = frame.state
      this.freshThread = frame.state === null
      void this.consume(reader, pending)
      return frame.state
    }
  }

  private async consume(
    reader: ReadableStreamDefaultReader<string>,
    pending: string
  ) {
    try {
      while (true) {
        const newline = pending.indexOf("\n")
        if (newline >= 0) {
          const frame = JSON.parse(pending.slice(0, newline)) as Frame
          pending = pending.slice(newline + 1)
          if (frame.type === "reset") throw new Error(frame.reason)
          if (frame.type === "event") this.publish(frame.event)
          continue
        }
        const next = await reader.read()
        if (next.done) throw new Error("Thread snapshot stream closed")
        pending += next.value
      }
    } catch {
      this.closeSubscribers()
    }
  }

  private publish(event: ProtocolEvent) {
    if (event.type !== "event") return
    const eventId = event.event_id
    if (!eventId || this.seen.has(eventId)) return
    this.seen.add(eventId)
    this.eventsBuffer.push(event)
    this.eventBytes += JSON.stringify(event).length
    if (this.eventsBuffer.length > MAX_EVENTS || this.eventBytes > MAX_BYTES) {
      this.closeSubscribers()
      return
    }
    for (const subscriber of this.subscribers) {
      if (matchesSubscription(event, subscriber.params))
        subscriber.queue.push(event)
    }
  }

  private closeSubscribers() {
    for (const subscriber of this.subscribers) subscriber.queue.close()
    this.subscribers.clear()
  }

  private reset() {
    this.controller?.abort()
    this.controller = undefined
    this.snapshot = undefined
    this.snapshotPromise = undefined
    this.eventsBuffer = []
    this.eventBytes = 0
    this.seen.clear()
    this.freshThread = false
    this.closeSubscribers()
  }
}
