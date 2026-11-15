import { useSyncExternalStore } from "react"
import type { AgentThread } from "@/features/agents/lib/types"

interface PendingThreadStart {
  thread: AgentThread
  userId: string
  phase: "starting" | "stopping" | "accepted"
}

class PendingThreadStarts {
  private state: {
    entries: Array<PendingThreadStart>
    submissionId: string | null
  } = { entries: [], submissionId: null }
  private listeners = new Set<() => void>()

  read = () => this.state

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private update(state: typeof this.state) {
    this.state = state
    this.listeners.forEach((listener) => listener())
  }

  get(threadId: string) {
    return this.state.entries.find((entry) => entry.thread.id === threadId)
  }

  add(thread: AgentThread, userId: string) {
    this.update({
      entries: [...this.state.entries, { thread, userId, phase: "starting" }],
      submissionId: thread.id,
    })
  }

  detach() {
    this.update({ ...this.state, submissionId: null })
  }

  accept(thread: AgentThread) {
    this.update({
      ...this.state,
      entries: this.state.entries.map((entry) =>
        entry.thread.id === thread.id
          ? { ...entry, thread, phase: "accepted" }
          : entry
      ),
    })
  }

  reconcile(threads: ReadonlyArray<AgentThread>) {
    const ids = new Set(threads.map((thread) => thread.id))
    const entries = this.state.entries.filter(
      (entry) => entry.phase !== "accepted" || !ids.has(entry.thread.id)
    )
    if (entries.length !== this.state.entries.length)
      this.update({ ...this.state, entries })
  }

  remove(threadId: string) {
    this.update({
      entries: this.state.entries.filter(
        (entry) => entry.thread.id !== threadId
      ),
      submissionId:
        this.state.submissionId === threadId ? null : this.state.submissionId,
    })
  }

  stop(threadId: string) {
    this.update({
      ...this.state,
      entries: this.state.entries.map((entry) =>
        entry.thread.id === threadId && entry.phase === "starting"
          ? { ...entry, phase: "stopping" }
          : entry
      ),
    })
  }

  reorderableIds(threads: ReadonlyArray<AgentThread>) {
    return threads
      .filter((thread) => {
        const entry = this.get(thread.id)
        return !entry || entry.phase === "accepted"
      })
      .map((thread) => thread.id)
  }

  merge(
    threads: ReadonlyArray<AgentThread>,
    fallbacks: ReadonlyArray<AgentThread>
  ) {
    const merged = new Map(fallbacks.map((thread) => [thread.id, thread]))
    for (const thread of threads) {
      const fallback = merged.get(thread.id)
      const entry = this.get(thread.id)
      merged.delete(thread.id)
      merged.set(
        thread.id,
        fallback && entry && entry.phase !== "accepted" ? fallback : thread
      )
    }
    return [...merged.values()]
  }
}

export const pendingThreadStarts = new PendingThreadStarts()

export function usePendingThreadStarts() {
  return useSyncExternalStore(
    pendingThreadStarts.subscribe,
    pendingThreadStarts.read,
    pendingThreadStarts.read
  )
}
