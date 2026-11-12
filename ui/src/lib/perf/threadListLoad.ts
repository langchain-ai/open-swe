import { useEffect, useEffectEvent, useRef } from "react"

import { startSpan, type SpanHandle } from "./trace"

type ListScope = "pinned" | "recents" | "repositories" | "folder"

class ThreadListLoad {
  private readonly span: SpanHandle
  private frame: number | undefined

  constructor(scope: ListScope, cached: boolean) {
    this.span = startSpan("thread_list_load", { scope, cached })
  }

  painted(items: number, failed: boolean): void {
    if (this.span.ended) return
    this.span.mark("data")
    this.frame = requestAnimationFrame(() => {
      this.frame = requestAnimationFrame(() => {
        this.span.mark("paint")
        this.span.end({ items, outcome: failed ? "error" : "success" })
      })
    })
  }

  cancelPaint(): void {
    if (this.frame !== undefined) cancelAnimationFrame(this.frame)
    this.frame = undefined
  }

  dispose(): void {
    this.cancelPaint()
    this.span.abandon("unmounted")
  }
}

export function useThreadListLoad({
  scope,
  identity,
  enabled,
  pending,
  failed,
  items,
}: {
  scope: ListScope
  identity: string
  enabled: boolean
  pending: boolean
  failed: boolean
  items: number
}): void {
  const load = useRef<ThreadListLoad | null>(null)
  const begin = useEffectEvent(() => new ThreadListLoad(scope, !pending))
  useEffect(() => {
    if (!enabled) return
    const current = begin()
    load.current = current
    return () => {
      current.dispose()
      load.current = null
    }
  }, [scope, identity, enabled])

  useEffect(() => {
    const current = load.current
    if (!current || pending) return
    current.painted(items, failed)
    return () => current.cancelPaint()
  }, [scope, identity, enabled, pending, failed, items])
}
