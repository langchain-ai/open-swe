import { useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { useStreamPool } from "@/features/agents/lib/stream/streamPool"
import type { DesktopNetworkResumeEvent } from "@/desktop"

export const NETWORK_STATUS_TOAST_ID = "open-swe-network-status"
export const NETWORK_RESUME_TOAST_ID = "open-swe-network-resume"

export const NETWORK_OFFLINE_MESSAGE =
  "Network connection lost. Retrying automatically when the connection returns."

export const NETWORK_RESUME_MESSAGE =
  "Lost the connection to the local backend. Continuing automatically when it returns."

export const NETWORK_RESUME_TIMEOUT_MESSAGE =
  "Gave up waiting for the local backend. Send a message to pick the task back up."

const NETWORK_RESTORED_MESSAGE = "Back online. Refreshing…"

/**
 * Keeps the app usable across a dropped connection: a persistent notice while
 * offline, then a refetch of active queries and an immediate reconnect of any
 * agent stream that was waiting for its next backoff attempt.
 *
 * The desktop also runs a recovery coordinator for local runs. It reports which
 * turns are waiting on a reachable backend, and that report — not a guess based
 * on the SDK's retry countdown — drives the connection state the UI shows.
 */
export function NetworkStatusWatcher() {
  const queryClient = useQueryClient()
  const wakeReconnecting = useStreamPool((state) => state.wakeReconnecting)
  const resumeConnection = useStreamPool((state) => state.resumeConnection)

  useEffect(() => {
    const bridge = window.openSweDesktop?.networkResume
    if (!bridge) return
    let disposed = false

    const apply = ({ snapshot, change }: DesktopNetworkResumeEvent) => {
      if (disposed || !snapshot?.enabled) return
      const waiting = snapshot.chains.filter(
        (chain) => chain.phase === "waiting"
      )
      for (const chain of waiting) {
        resumeConnection(chain.threadId, {
          status: "reconnecting",
          attempt: chain.generation,
          retryAt: 0,
          source: "resume",
        })
      }
      if (waiting.length) {
        toast.info(NETWORK_RESUME_MESSAGE, {
          id: NETWORK_RESUME_TOAST_ID,
          duration: Infinity,
          closeButton: false,
        })
      } else {
        toast.dismiss(NETWORK_RESUME_TOAST_ID)
      }
      if (
        change?.kind === "chain-updated" &&
        snapshot.chains.some(
          (chain) => chain.phase === "dispatching" && chain.dispatched
        )
      ) {
        // The continuation run exists, so reconnect now instead of waiting out
        // whatever is left of the SDK's backoff. `dispatched` matters: remount
        // before the run's thread exists and the new stream hydrates a 404,
        // which parks its pump forever.
        wakeReconnecting()
      }
      if (change?.kind === "chain-removed" && change.outcome === "timed-out") {
        resumeConnection(change.threadId, {
          status: "failed",
          message: NETWORK_RESUME_TIMEOUT_MESSAGE,
        })
        toast.error(NETWORK_RESUME_TIMEOUT_MESSAGE, {
          id: NETWORK_RESUME_TOAST_ID,
          duration: 10_000,
        })
      }
    }

    const unsubscribe = bridge.onChange(apply)
    void bridge
      .snapshot()
      .then((snapshot) => {
        if (snapshot) apply({ snapshot, change: null })
      })
      .catch(() => {})
    return () => {
      disposed = true
      unsubscribe()
      toast.dismiss(NETWORK_RESUME_TOAST_ID)
    }
  }, [resumeConnection, wakeReconnecting])

  useEffect(() => {
    let offline = navigator.onLine === false

    const showOffline = () => {
      toast.info(NETWORK_OFFLINE_MESSAGE, {
        id: NETWORK_STATUS_TOAST_ID,
        duration: Infinity,
        closeButton: false,
      })
    }

    const handleOffline = () => {
      offline = true
      showOffline()
    }

    const handleOnline = () => {
      const recovered = offline
      offline = false
      if (recovered) {
        toast.dismiss(NETWORK_STATUS_TOAST_ID)
        toast.success(NETWORK_RESTORED_MESSAGE, { duration: 3_000 })
      }
      void queryClient.refetchQueries({ type: "active" })
      wakeReconnecting()
    }

    if (offline) showOffline()
    window.addEventListener("offline", handleOffline)
    window.addEventListener("online", handleOnline)
    return () => {
      window.removeEventListener("offline", handleOffline)
      window.removeEventListener("online", handleOnline)
      toast.dismiss(NETWORK_STATUS_TOAST_ID)
    }
  }, [queryClient, wakeReconnecting])

  return null
}
