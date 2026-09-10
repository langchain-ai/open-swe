import { create } from "zustand"
import type { UseStreamReturn } from "@langchain/react"

export type AgentStream = UseStreamReturn

export type AgentThreadTransport = "cloud" | "local"

export interface StreamPoolEntry {
  /** Stable identity for the mounted `useStream` instance. */
  id: string
  transport: AgentThreadTransport
  /** `null` until the first submit mints a thread id. */
  threadId: string | null
  /** Created without a thread; its first accepted run is the thread's creation. */
  awaitingCreation: boolean
  /** Bumped by `kick` to remount the SDK instance; the handle stays published until the replacement publishes. */
  generation: number
  lastActiveAt: number
}

export interface StreamBinding {
  transport: AgentThreadTransport
  threadId: string | null
}

export const IDLE_STREAM_TTL_MS = 60_000
export const MAX_IDLE_STREAMS = 8

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
  consumeCreatedThread(): void
  /** Remount a thread's instance so it re-hydrates and reopens its event stream. */
  kick(transport: AgentThreadTransport, threadId: string): void
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
      generation: 0,
      lastActiveAt: now,
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

  consumeCreatedThread() {
    set({ createdThreadId: null })
  },

  kick(transport, threadId) {
    set((state) => ({
      entries: state.entries.map((entry) =>
        entry.transport === transport && entry.threadId === threadId
          ? { ...entry, generation: entry.generation + 1 }
          : entry
      ),
    }))
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
  const entry = retained ?? bound
  return entry ? state.handles[entry.id] : undefined
}
