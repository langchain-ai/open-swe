import type { UseStreamReturn } from "@langchain/react"

/** The SDK stream handle, plus the run facts we track outside its state. */
export type AgentStream = UseStreamReturn & {
  isOffloading?: boolean
  /** Route/model the Auto router picked for the latest run, when known. */
  routed?: { route?: string; modelId?: string | null } | null
}

export type AgentThreadTransport = "cloud" | "local"

/** Liveness of a thread's event stream. */
export type StreamConnection =
  | { status: "live" }
  | { status: "reconnecting"; attempt: number; retryAt: number }

export const LIVE_CONNECTION: StreamConnection = { status: "live" }

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
