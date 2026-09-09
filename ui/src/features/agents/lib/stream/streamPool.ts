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
  lastActiveAt: number
}

export const IDLE_STREAM_TTL_MS = 60_000
export const MAX_IDLE_STREAMS = 8

export interface StreamBinding {
  transport: AgentThreadTransport
  threadId: string | null
}

interface StreamPoolState {
  entries: Array<StreamPoolEntry>
  handles: Record<string, AgentStream | undefined>
  activeId: string | null
  /** What the route last asked for; stays `null`-threaded while a lazy thread awaits navigation. */
  binding: StreamBinding | null
  /** Bind the pool to a thread, reusing a retained instance when there is one. */
  activate(transport: AgentThreadTransport, threadId: string | null): void
  publish(id: string, handle: AgentStream): void
  /** A lazily created thread received its server id. */
  rekey(id: string, threadId: string): void
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
