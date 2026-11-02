import { useEffect, useMemo, useSyncExternalStore } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { WorkThreadCache } from "./queries"
import { useIsInAgentThreadStream } from "./provider/useIsInAgentThreadStream"
import type { AgentThread } from "./types"
import type { ThreadSource } from "./threadSource/types"

class ForegroundDocument {
  private readonly listeners = new Set<() => void>()

  private notify = () => this.listeners.forEach((listener) => listener())

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    window.addEventListener("focus", this.notify)
    window.addEventListener("blur", this.notify)
    document.addEventListener("visibilitychange", this.notify)
    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size) return
      window.removeEventListener("focus", this.notify)
      window.removeEventListener("blur", this.notify)
      document.removeEventListener("visibilitychange", this.notify)
    }
  }

  snapshot = () => document.visibilityState === "visible" && document.hasFocus()

  serverSnapshot = () => false
}

const foregroundDocument = new ForegroundDocument()

class DisplayedRunRead {
  private disposed = false
  private pending = false
  private attempt = 0
  private frame: number | null = null
  private retry: ReturnType<typeof setTimeout> | null = null

  constructor(
    private readonly summaries: WorkThreadCache,
    private readonly runId: string
  ) {}

  start = () => {
    if (this.disposed || this.pending) return
    this.attempt = 0
    if (this.retry !== null) clearTimeout(this.retry)
    if (this.frame !== null) cancelAnimationFrame(this.frame)
    this.frame = requestAnimationFrame(this.read)
  }

  private read = () => {
    if (this.disposed || this.pending || !foregroundDocument.snapshot()) return
    this.pending = true
    void this.summaries
      .acknowledge(this.runId)
      .catch((error: unknown) => {
        console.warn("Could not mark the Work result read", { error })
        if (!this.disposed && this.attempt < 2) {
          this.attempt += 1
          this.retry = setTimeout(this.read, this.attempt * 1000)
        }
      })
      .finally(() => {
        this.pending = false
      })
  }

  dispose(): void {
    this.disposed = true
    if (this.frame !== null) cancelAnimationFrame(this.frame)
    if (this.retry !== null) clearTimeout(this.retry)
  }
}

export function useWorkThreadRead(thread: AgentThread, source: ThreadSource) {
  const active = useIsInAgentThreadStream()
  const queryClient = useQueryClient()
  const summaries = useMemo(
    () => new WorkThreadCache(queryClient, thread.id),
    [queryClient, thread.id]
  )
  const foreground = useSyncExternalStore(
    foregroundDocument.subscribe,
    foregroundDocument.snapshot,
    foregroundDocument.serverSnapshot
  )
  const runId = source.kind === "transcript" ? source.settledRunId : null
  const readable =
    active &&
    thread.experience === "agent_workspace" &&
    !thread.viewed &&
    thread.status !== "running" &&
    !source.isRunning &&
    !source.isHydrating &&
    !source.error &&
    source.threadId === thread.id &&
    runId === thread.latestRunId

  useEffect(() => {
    if (!foreground || !readable || !runId) return
    const read = new DisplayedRunRead(summaries, runId)
    read.start()
    window.addEventListener("online", read.start)
    return () => {
      window.removeEventListener("online", read.start)
      read.dispose()
    }
  }, [foreground, readable, runId, summaries])
}
