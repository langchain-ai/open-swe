import { agentsApi } from "./api"
import type { AgentThread } from "./types"

export interface ThreadTransport {
  streamBase(thread?: AgentThread): { apiUrl: string; assistantId: string }
  canExecuteHere(thread: AgentThread): boolean
}

export const cloudThreadTransport: ThreadTransport = {
  streamBase: () => ({
    apiUrl: agentsApi.langGraphApiUrl,
    assistantId: "agent",
  }),
  canExecuteHere: () => true,
}

export const localThreadTransport: ThreadTransport = {
  streamBase: () => ({ apiUrl: "/local-graph", assistantId: "agent" }),
  canExecuteHere: (thread) =>
    Boolean(
      window.openSweDesktop &&
      thread.deviceId &&
      window.openSweDesktop.deviceId === thread.deviceId
    ),
}

export function transportForThread(thread?: AgentThread): ThreadTransport {
  return thread?.environment === "local"
    ? localThreadTransport
    : cloudThreadTransport
}
