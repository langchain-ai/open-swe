import { useCallback, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useRouter } from "@tanstack/react-router"

import { captureDebugSnapshot, formatDebugSnapshot } from "@/lib/debugSnapshot"
import { useRapidKeyRun } from "@/lib/hotkeys"

const STATUS_TIMEOUT_MS = 3500

interface DumpStatus {
  copied: boolean
  kb: number
  errors: number
}

/** Triple-press Escape to copy a snapshot of the page's state for a bug report. */
export function DebugSnapshotHotkey() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<DumpStatus | null>(null)

  const dump = useCallback(async () => {
    const snapshot = captureDebugSnapshot({
      router,
      queryClient,
      trigger: "triple-escape",
    })
    const text = formatDebugSnapshot(snapshot)
    ;(window as Window & { __openSweDebug?: unknown }).__openSweDebug = {
      snapshot,
      text,
    }

    console.groupCollapsed(
      `Open SWE debug snapshot · ${snapshot.route.pathname} · ${snapshot.meta.atLocal}`
    )
    console.log(snapshot)
    console.log(text)
    console.log("kept on window.__openSweDebug")
    console.groupEnd()

    let copied = false
    try {
      await navigator.clipboard.writeText(text)
      copied = true
    } catch {
      copied = false
    }
    setStatus({
      copied,
      kb: Math.max(1, Math.round(text.length / 1024)),
      errors: snapshot.errors.length,
    })
  }, [queryClient, router])

  useRapidKeyRun("Escape", () => void dump())

  useEffect(() => {
    if (!status) return
    const timer = window.setTimeout(() => setStatus(null), STATUS_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [status])

  if (!status) return null
  return (
    <div
      role="status"
      className="pointer-events-none fixed bottom-4 left-1/2 z-100 -translate-x-1/2 rounded-md border border-border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md"
    >
      {status.copied
        ? `Debug snapshot copied (${status.kb} KB${status.errors ? `, ${status.errors} JS errors` : ""}) — also in the console`
        : "Debug snapshot logged to the console — clipboard copy was blocked"}
    </div>
  )
}
