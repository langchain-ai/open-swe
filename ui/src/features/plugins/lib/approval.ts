import { useRef, useState } from "react"

import type { ThreadRequest } from "@/features/plugins/lib/bridge"
import { BridgeRequestError } from "@/features/plugins/lib/bridge"

export type Approval =
  | { kind: "thread"; request: ThreadRequest }
  | { kind: "link"; url: string }

/** How long the approve button stays disabled once a request is shown. */
const ARM_DELAY_MS = 600

/**
 * One question to the person at a time. While it is open, a plugin's next
 * request is refused rather than queued or swapped in, so what they approve
 * is exactly what they read; approving only works once it has been on screen
 * a moment, so a click aimed at something else cannot land on it.
 */
export function useApproval() {
  const open = useRef<{
    approval: Approval
    resolve: (ok: boolean) => void
  } | null>(null)
  const [shown, setShown] = useState<Approval | null>(null)
  const [armed, setArmed] = useState(false)

  const ask = (approval: Approval): Promise<boolean> => {
    if (open.current)
      return Promise.reject(
        new BridgeRequestError("Another request is waiting for an answer", 409)
      )
    return new Promise((resolve) => {
      open.current = { approval, resolve }
      setShown(approval)
      setArmed(false)
      setTimeout(() => setArmed(true), ARM_DELAY_MS)
    })
  }

  const answer = (ok: boolean) => {
    const held = open.current
    open.current = null
    setShown(null)
    held?.resolve(ok)
  }

  return { shown, armed, ask, answer }
}
