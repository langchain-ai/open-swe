import type { Thread } from "@langchain/langgraph-sdk"
export type { Thread }
import { Client } from "@langchain/langgraph-sdk"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"

export const FLEET_ASSISTANT_ID = "47c35ef5-979e-5ab8-8dce-a69ded2244ab"

export interface Workspace {
  id: string
  name: string
}
export function fleetClient(workspaceId: string) {
  return new Client({
    apiUrl: new URL(
      dashboardApiUrl(`/fleet-chat/${workspaceId}`),
      window.location.origin
    ).href,
    callerOptions: {
      fetch: (input: RequestInfo | URL, init?: RequestInit) =>
        fetch(input, { ...init, credentials: "include" }),
    },
  })
}

export async function fleetWorkspaces(): Promise<Workspace[]> {
  const response = await fetch(dashboardApiUrl("/fleet-chat/workspaces"), {
    credentials: "include",
  })
  if (!response.ok) {
    const error: { detail?: string } = await response.json()
    throw new Error(error.detail ?? "LangSmith workspaces could not be loaded.")
  }
  return response.json()
}
