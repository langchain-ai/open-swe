import { create } from "zustand"
import type { UseStreamReturn } from "@langchain/react"

export type AgentStream = UseStreamReturn & {
  isOffloading?: boolean
  /** Route/model the Auto router picked for the latest run, when known. */
  routed?: { route?: string; modelId?: string | null } | null
}

export type AgentThreadTransport = "cloud" | "local"

/** Liveness of the bound stream's event subscription. */
export type StreamConnection =
  | { status: "live" }
  | { status: "reconnecting"; attempt: number; retryAt: number }

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

export const LIVE: StreamConnection = { status: "live" }

export interface StreamConnectionState {
  connection: StreamConnection
  /** The transport is about to retry; `attempt` is 1-based. */
  reconnecting(attempt: number, retryAt: number): void
  /** An event stream opened or its run ended, so it is no longer reconnecting. */
  live(): void
}

export const useStreamConnection = create<StreamConnectionState>((set) => ({
  connection: LIVE,
  reconnecting(attempt, retryAt) {
    set({ connection: { status: "reconnecting", attempt, retryAt } })
  },
  live() {
    set((state) =>
      state.connection.status === "live" ? state : { connection: LIVE }
    )
  },
}))
