import type { QueryClient } from "@tanstack/react-query"

import { dashboardApiUrl } from "@/lib/dashboard-fetch"

import type { InvalidationTopic } from "./topics"

/** Per topic, the epoch ms from which this browser does not know what changed. */
type Since = Map<InvalidationTopic, number>

export interface InvalidationHello {
  backend_commit: string | null
  /** Topics invalidated while this browser was not listening. */
  invalidated: InvalidationTopic[]
  /** Topics this session may not hear; they are not asked for again. */
  denied: InvalidationTopic[]
}

type TabMessage =
  | { kind: "interest"; tab: string; since: Record<InvalidationTopic, number> }
  | { kind: "leave"; tab: string }
  | { kind: "census" }
  | { kind: "invalidated"; topics: InvalidationTopic[] }
  | { kind: "covered"; topics: InvalidationTopic[]; at: number }
  | { kind: "hello"; hello: InvalidationHello }

const CHANNEL_NAME = "open-swe-invalidations"
const LOCK_NAME = "open-swe-invalidations-leader"
const REPORT_DEBOUNCE_MS = 100
const CONNECT_DEBOUNCE_MS = 150
/** The server sends a frame at least every 15s; three missed means the stream is dead. */
const SILENCE_LIMIT_MS = 45_000
const MIN_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000

/**
 * One browser tab's part in the dashboard's single invalidation stream.
 *
 * Tabs elect a leader with the Web Locks API. Only the leader holds an
 * `EventSource`; the others tell it which topics their mounted queries read
 * and hear its invalidations over a `BroadcastChannel`. Every tab refetches
 * its own queries when a topic they read is invalidated.
 */
export class InvalidationTab {
  private readonly id = crypto.randomUUID()
  /** Per topic, the last time an open stream was known to cover it. */
  private readonly covered: Since = new Map()
  private channel: BroadcastChannel | null = null
  private leader: Leader | null = null
  private releaseLock: (() => void) | null = null
  private reportedKey: string | null = null
  private reportTimer: ReturnType<typeof setTimeout> | null = null
  private unsubscribeCache: (() => void) | null = null
  private stopped = false

  constructor(
    private readonly queryClient: QueryClient,
    private readonly onHello: (hello: InvalidationHello) => void
  ) {}

  start(): void {
    this.unsubscribeCache = this.queryClient
      .getQueryCache()
      .subscribe((event) => {
        if (
          event.type === "added" ||
          event.type === "removed" ||
          event.type === "observerAdded" ||
          event.type === "observerRemoved"
        ) {
          this.scheduleReport()
        }
      })
    if (typeof BroadcastChannel === "undefined" || !navigator.locks) {
      this.lead()
      return
    }
    this.channel = new BroadcastChannel(CHANNEL_NAME)
    this.channel.onmessage = (event: MessageEvent<TabMessage>) =>
      this.receive(event.data)
    window.addEventListener("pagehide", this.leave)
    void navigator.locks.request(
      LOCK_NAME,
      () =>
        new Promise<void>((release) => {
          if (this.stopped) {
            release()
            return
          }
          this.releaseLock = release
          this.lead()
        })
    )
    this.scheduleReport()
  }

  stop(): void {
    this.stopped = true
    this.unsubscribeCache?.()
    if (this.reportTimer) clearTimeout(this.reportTimer)
    this.leave()
    window.removeEventListener("pagehide", this.leave)
    this.leader?.close()
    this.leader = null
    this.releaseLock?.()
    this.channel?.close()
  }

  private readonly leave = () => {
    if (!this.leader) this.post({ kind: "leave", tab: this.id })
  }

  private lead(): void {
    this.leader = new Leader(this.covered, {
      onHello: (hello) => {
        this.onHello(hello)
        this.post({ kind: "hello", hello })
      },
      onInvalidated: (topics) => {
        this.invalidate(topics)
        this.post({ kind: "invalidated", topics })
      },
      onCovered: (topics, at) => {
        this.cover(topics, at)
        this.post({ kind: "covered", topics, at })
      },
    })
    this.leader.setInterest(this.id, this.localSince())
    this.post({ kind: "census" })
  }

  private receive(message: TabMessage): void {
    switch (message.kind) {
      case "interest":
        this.leader?.setInterest(
          message.tab,
          new Map(Object.entries(message.since))
        )
        return
      case "leave":
        this.leader?.removeInterest(message.tab)
        return
      case "census":
        if (!this.leader) this.report(true)
        return
      case "invalidated":
        this.invalidate(message.topics)
        return
      case "covered":
        this.cover(message.topics, message.at)
        return
      case "hello":
        this.onHello(message.hello)
        return
    }
  }

  private post(message: TabMessage): void {
    this.channel?.postMessage(message)
  }

  private scheduleReport(): void {
    if (this.reportTimer) clearTimeout(this.reportTimer)
    this.reportTimer = setTimeout(() => this.report(false), REPORT_DEBOUNCE_MS)
  }

  /** Tell the leader which topics this tab reads, when that set changes. */
  private report(force: boolean): void {
    if (this.stopped) return
    const since = this.localSince()
    const key = topicsKey(since.keys())
    if (!force && key === this.reportedKey) return
    this.reportedKey = key
    if (this.leader) {
      this.leader.setInterest(this.id, since)
      return
    }
    this.post({
      kind: "interest",
      tab: this.id,
      since: Object.fromEntries(since),
    })
  }

  /**
   * Each topic an observed query reads, with the oldest moment any of them
   * was last known current: when its data arrived, or later if an open stream
   * has covered the topic since. A query still loading counts as current.
   */
  private localSince(): Since {
    const now = Date.now()
    const since: Since = new Map()
    for (const query of this.queryClient.getQueryCache().getAll()) {
      const topics = query.meta?.invalidatedBy
      if (!topics?.length || query.getObserversCount() === 0) continue
      const fetched = query.state.dataUpdatedAt || now
      for (const topic of topics) {
        const known = Math.max(fetched, this.covered.get(topic) ?? 0)
        since.set(topic, Math.min(since.get(topic) ?? Infinity, known))
      }
    }
    return since
  }

  private invalidate(topics: readonly InvalidationTopic[]): void {
    if (!topics.length) return
    const invalidated = new Set(topics)
    void this.queryClient.invalidateQueries({
      predicate: (query) =>
        query.meta?.invalidatedBy?.some((topic) => invalidated.has(topic)) ??
        false,
    })
  }

  private cover(topics: readonly InvalidationTopic[], at: number): void {
    for (const topic of topics) {
      this.covered.set(topic, Math.max(this.covered.get(topic) ?? 0, at))
    }
  }
}

interface LeaderEvents {
  onHello: (hello: InvalidationHello) => void
  onInvalidated: (topics: InvalidationTopic[]) => void
  onCovered: (topics: InvalidationTopic[], at: number) => void
}

/**
 * Holds the stream for every tab. A changed topic set opens a new stream
 * before closing the old one, so no change falls between them.
 */
class Leader {
  private readonly interests = new Map<string, Since>()
  private readonly denied = new Set<InvalidationTopic>()
  private current: Connection | null = null
  private pending: Connection | null = null
  private connectTimer: ReturnType<typeof setTimeout> | null = null
  private backoff = MIN_BACKOFF_MS
  private closed = false

  constructor(
    private readonly covered: Since,
    private readonly events: LeaderEvents
  ) {}

  setInterest(tab: string, since: Since): void {
    this.interests.set(tab, since)
    this.scheduleConnect(CONNECT_DEBOUNCE_MS)
  }

  removeInterest(tab: string): void {
    if (this.interests.delete(tab)) this.scheduleConnect(CONNECT_DEBOUNCE_MS)
  }

  close(): void {
    this.closed = true
    if (this.connectTimer) clearTimeout(this.connectTimer)
    this.current?.close()
    this.pending?.close()
  }

  private scheduleConnect(delay: number): void {
    if (this.closed) return
    if (this.connectTimer) clearTimeout(this.connectTimer)
    this.connectTimer = setTimeout(() => this.connect(), delay)
  }

  private connect(): void {
    this.connectTimer = null
    const since = this.mergedSince()
    const key = topicsKey(since.keys())
    if ((this.pending ?? this.current)?.key === key) return
    this.pending?.close()
    const connection: Connection = new Connection(since, {
      onHello: (hello) => this.opened(connection, hello),
      onInvalidated: (topics) => {
        if (connection !== this.current) return
        this.events.onInvalidated(topics)
        this.events.onCovered(connection.topics, Date.now())
      },
      onAlive: () => {
        if (connection !== this.current) return
        this.events.onCovered(connection.topics, Date.now())
      },
      onError: () => this.failed(connection),
    })
    this.pending = connection
  }

  private opened(connection: Connection, hello: InvalidationHello): void {
    if (connection !== this.pending) {
      connection.close()
      return
    }
    for (const topic of hello.denied) this.denied.add(topic)
    this.current?.close()
    this.current = connection
    this.pending = null
    this.backoff = MIN_BACKOFF_MS
    this.events.onHello(hello)
    this.events.onInvalidated(hello.invalidated)
    this.events.onCovered(
      connection.topics.filter((topic) => !this.denied.has(topic)),
      Date.now()
    )
  }

  private failed(connection: Connection): void {
    connection.close()
    if (connection === this.pending) this.pending = null
    else if (connection === this.current) this.current = null
    else return
    const jitter = Math.random() * this.backoff * 0.3
    this.scheduleConnect(this.backoff + jitter)
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
  }

  /** The oldest unknown moment per topic across tabs, never before the stream last covered it. */
  private mergedSince(): Since {
    const merged: Since = new Map()
    for (const since of this.interests.values()) {
      for (const [topic, at] of since) {
        if (this.denied.has(topic)) continue
        merged.set(topic, Math.min(merged.get(topic) ?? Infinity, at))
      }
    }
    for (const [topic, at] of merged) {
      merged.set(topic, Math.max(at, this.covered.get(topic) ?? 0))
    }
    return merged
  }
}

interface ConnectionEvents {
  onHello: (hello: InvalidationHello) => void
  onInvalidated: (topics: InvalidationTopic[]) => void
  onAlive: () => void
  onError: () => void
}

/**
 * One `EventSource`. Its own retry is never used: it would reopen with the
 * replay windows of the original URL, which shrink to nothing as time passes,
 * so a dropped stream is closed and the leader opens a new one.
 */
class Connection {
  readonly key: string
  readonly topics: InvalidationTopic[]
  private readonly source: EventSource
  private watchdog: ReturnType<typeof setTimeout> | null = null
  private closed = false

  constructor(
    since: Since,
    private readonly events: ConnectionEvents
  ) {
    this.topics = [...since.keys()].sort()
    this.key = topicsKey(this.topics)
    const now = Date.now()
    const params = new URLSearchParams()
    for (const topic of this.topics) {
      const age = Math.max(0, Math.ceil((now - since.get(topic)!) / 1000))
      params.append("t", `${age}.${topic}`)
    }
    const search = params.toString()
    const query = search ? `?${search}` : ""
    this.source = new EventSource(
      dashboardApiUrl(`/ui-invalidations${query}`),
      {
        withCredentials: true,
      }
    )
    this.source.addEventListener("hello", (event) =>
      this.frame(event, (data: InvalidationHello) => events.onHello(data))
    )
    this.source.addEventListener("invalidated", (event) =>
      this.frame(event, (data: { topics: InvalidationTopic[] }) =>
        events.onInvalidated(data.topics)
      )
    )
    this.source.addEventListener("alive", (event) =>
      this.frame(event, () => events.onAlive())
    )
    this.source.addEventListener("error", () => this.fail())
    this.arm()
  }

  close(): void {
    this.closed = true
    if (this.watchdog) clearTimeout(this.watchdog)
    this.source.close()
  }

  private frame<T>(event: MessageEvent<string>, apply: (data: T) => void) {
    if (this.closed) return
    this.arm()
    try {
      apply(JSON.parse(event.data) as T)
    } catch (error) {
      console.warn("Unreadable invalidation frame; reconnecting", error)
      this.fail()
    }
  }

  private arm(): void {
    if (this.watchdog) clearTimeout(this.watchdog)
    this.watchdog = setTimeout(() => this.fail(), SILENCE_LIMIT_MS)
  }

  private fail(): void {
    if (this.closed) return
    this.close()
    this.events.onError()
  }
}

function topicsKey(topics: Iterable<InvalidationTopic>): string {
  return [...topics].sort().join("\n")
}
