import { create } from "zustand"
import type { UseStreamReturn } from "@langchain/react"

export type AgentStream = UseStreamReturn & {
  isOffloading?: boolean
  /** Route/model the Auto router picked for the latest run, when known. */
  routed?: { route?: string; modelId?: string | null } | null
}

export type AgentThreadTransport = "cloud" | "local"

/** Who decided the stream is down: the SDK's own backoff, or a resume chain. */
export type StreamConnectionSource = "sdk" | "resume"

/** Liveness of an instance's event stream. */
export type StreamConnection =
  | { status: "live" }
  | {
      status: "reconnecting"
      attempt: number
      retryAt: number
      source: StreamConnectionSource
    }
  | { status: "failed"; message: string }

export interface StreamPoolEntry {
  /** Stable identity for the mounted `useStream` instance. */
  id: string
  transport: AgentThreadTransport
  /** `null` until the first submit mints a thread id. */
  threadId: string | null
  /** Created without a thread; its first accepted run is the thread's creation. */
  awaitingCreation: boolean
  lastActiveAt: number
  connection: StreamConnection
  /** Bumped to remount a stream and connect immediately after the network returns. */
  reconnectEpoch: number
}

export interface StreamBinding {
  transport: AgentThreadTransport
  threadId: string | null
}

export const IDLE_STREAM_TTL_MS = 60_000
export const MAX_IDLE_STREAMS = 8

/**
 * How long a loading stream may produce no events before the desktop is asked
 * whether its backend is still there. Long enough for a quiet tool call, short
 * enough that a blackholed link never reads as permanent work.
 */
export const STALL_QUIET_MS = 60_000

/** 1s doubling to a 5min ceiling spends this budget over roughly 23 minutes. */
export const MAX_RECONNECT_ATTEMPTS = 12

const RECONNECT_BASE_DELAY_MS = 1_000
const RECONNECT_MAX_DELAY_MS = 300_000

export function reconnectDelayMs(attempt: number): number {
  return Math.min(
    RECONNECT_BASE_DELAY_MS * 2 ** (attempt - 1),
    RECONNECT_MAX_DELAY_MS
  )
}

const LIVE: StreamConnection = { status: "live" }

export interface StreamPoolState {
  entries: Array<StreamPoolEntry>
  handles: Record<string, AgentStream | undefined>
  activeId: string | null
  /** What the route last asked for; stays `null`-threaded while a lazy thread awaits navigation. */
  binding: StreamBinding | null
  /** A lazily created thread the server just accepted, until the UI consumes it. */
  createdThreadId: string | null
  /** Bind the pool to a thread, reusing a retained instance when there is one. */
  activate(transport: AgentThreadTransport, threadId: string | null): void
  publish(id: string, handle: AgentStream): void
  /** A lazily created thread received its server id. */
  rekey(id: string, threadId: string): void
  /** The server accepted a run on this instance. */
  runAccepted(id: string): void
  /** The transport is about to retry; `attempt` is 1-based. */
  streamReconnecting(
    id: string,
    attempt: number,
    retryAt: number,
    source?: StreamConnectionSource
  ): void
  /**
   * A recovery chain owns the connection of the thread it belongs to. The
   * renderer does not decide when to retry; it renders what the coordinator
   * reports.
   */
  resumeConnection(threadId: string, connection: StreamConnection): void
  /** An event stream opened or its run ended, so it is no longer reconnecting. */
  streamLive(id: string): void
  /** Reconnect every stream waiting on a retry, without touching live ones. */
  wakeReconnecting(): void
  /** Remount one stream so it reconnects now, e.g. after a silent stall. */
  refreshStream(id: string): void
  consumeCreatedThread(): void
  /** Drop idle instances past the TTL or cap; active and running ones stay. */
  sweep(now: number): void
}

let nextId = 0

function isRetained(
  entry: StreamPoolEntry,
  state: StreamPoolState,
  now: number
): boolean {
  return (
    entry.id === state.activeId ||
    Boolean(state.handles[entry.id]?.isLoading) ||
    now - entry.lastActiveAt < IDLE_STREAM_TTL_MS
  )
}

export const useStreamPool = create<StreamPoolState>((set, get) => ({
  entries: [],
  handles: {},
  activeId: null,
  binding: null,
  createdThreadId: null,

  activate(transport, threadId) {
    const now = Date.now()
    const binding = { transport, threadId }
    const existing = get().entries.find(
      (entry) =>
        entry.transport === transport &&
        entry.threadId !== null &&
        entry.threadId === threadId
    )
    const touched = (entry: StreamPoolEntry) =>
      entry.id === get().activeId || entry.id === existing?.id
        ? { ...entry, lastActiveAt: now }
        : entry
    if (existing) {
      set((state) => ({
        activeId: existing.id,
        binding,
        entries: state.entries.map(touched),
      }))
      return
    }
    const entry: StreamPoolEntry = {
      id: `stream-${++nextId}`,
      transport,
      threadId,
      awaitingCreation: threadId === null,
      lastActiveAt: now,
      connection: LIVE,
      reconnectEpoch: 0,
    }
    set((state) => ({
      activeId: entry.id,
      binding,
      entries: [...state.entries.map(touched), entry],
    }))
    get().sweep(now)
  },

  publish(id, handle) {
    set((state) => ({ handles: { ...state.handles, [id]: handle } }))
  },

  rekey(id, threadId) {
    set((state) => {
      const target = state.entries.find((entry) => entry.id === id)
      if (!target) return state
      const duplicate = (entry: StreamPoolEntry) =>
        entry.id !== id &&
        entry.transport === target.transport &&
        entry.threadId === threadId
      return {
        entries: state.entries
          .filter((entry) => !duplicate(entry))
          .map((entry) => (entry.id === id ? { ...entry, threadId } : entry)),
      }
    })
  },

  runAccepted(id) {
    set((state) => {
      const entry = state.entries.find((candidate) => candidate.id === id)
      if (!entry?.awaitingCreation) return state
      // Only the cloud thread the user is still looking at may steer
      // navigation; a draft they left behind finishes creating quietly.
      const announce =
        entry.transport === "cloud" &&
        entry.id === state.activeId &&
        entry.threadId
      return {
        createdThreadId: announce ? entry.threadId : state.createdThreadId,
        entries: state.entries.map((candidate) =>
          candidate.id === id
            ? { ...candidate, awaitingCreation: false }
            : candidate
        ),
      }
    })
  },

  streamReconnecting(id, attempt, retryAt, source = "sdk") {
    set((state) => ({
      entries: state.entries.map((entry) =>
        entry.id === id
          ? {
              ...entry,
              connection: { status: "reconnecting", attempt, retryAt, source },
            }
          : entry
      ),
    }))
  },

  resumeConnection(threadId, connection) {
    set((state) => {
      let changed = false
      const entries = state.entries.map((entry) => {
        if (entry.threadId !== threadId) return entry
        changed = true
        return { ...entry, connection }
      })
      return changed ? { entries } : state
    })
  },

  streamLive(id) {
    set((state) => {
      const entry = state.entries.find((candidate) => candidate.id === id)
      if (!entry || entry.connection.status === "live") return state
      return {
        entries: state.entries.map((candidate) =>
          candidate.id === id ? { ...candidate, connection: LIVE } : candidate
        ),
      }
    })
  },

  wakeReconnecting() {
    set((state) => ({
      entries: state.entries.map((entry) =>
        entry.connection.status === "reconnecting"
          ? {
              ...entry,
              connection: LIVE,
              reconnectEpoch: entry.reconnectEpoch + 1,
            }
          : entry
      ),
    }))
  },

  refreshStream(id) {
    set((state) => ({
      entries: state.entries.map((entry) =>
        entry.id === id
          ? { ...entry, reconnectEpoch: entry.reconnectEpoch + 1 }
          : entry
      ),
    }))
  },

  consumeCreatedThread() {
    set({ createdThreadId: null })
  },

  sweep(now) {
    const state = get()
    const kept = state.entries.filter((entry) => isRetained(entry, state, now))
    const idle = kept
      .filter(
        (entry) =>
          entry.id !== state.activeId && !state.handles[entry.id]?.isLoading
      )
      .sort((a, b) => a.lastActiveAt - b.lastActiveAt)
    const evicted = new Set(
      idle
        .slice(0, Math.max(0, idle.length - MAX_IDLE_STREAMS))
        .map((e) => e.id)
    )
    const entries = kept.filter((entry) => !evicted.has(entry.id))
    if (entries.length === state.entries.length) return
    const live = new Set(entries.map((entry) => entry.id))
    set({
      entries,
      handles: Object.fromEntries(
        Object.entries(state.handles).filter(([id]) => live.has(id))
      ),
    })
  },
}))

/**
 * The stream serving a route's request. A retained thread is found by id in
 * the same render it is asked for; a thread-less request (home, lazily
 * creating) is whatever `activate` bound for it. Never the previous route's
 * handle by accident.
 */
export function selectStreamFor(
  state: StreamPoolState,
  transport: AgentThreadTransport,
  threadId: string | null
): AgentStream | undefined {
  const entry = selectEntryFor(state, transport, threadId)
  return entry ? state.handles[entry.id] : undefined
}

/** The connection of the instance `selectStreamFor` would return. */
export function selectConnectionFor(
  state: StreamPoolState,
  transport: AgentThreadTransport,
  threadId: string | null
): StreamConnection {
  return selectEntryFor(state, transport, threadId)?.connection ?? LIVE
}

function selectEntryFor(
  state: StreamPoolState,
  transport: AgentThreadTransport,
  threadId: string | null
): StreamPoolEntry | undefined {
  const retained =
    threadId === null
      ? undefined
      : state.entries.find(
          (entry) =>
            entry.transport === transport && entry.threadId === threadId
        )
  const bound =
    state.binding?.transport === transport &&
    state.binding.threadId === threadId
      ? state.entries.find((entry) => entry.id === state.activeId)
      : undefined
  return retained ?? bound
}
