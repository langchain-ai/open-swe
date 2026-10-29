import { Client } from "@langchain/langgraph-sdk"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"
export type { Thread } from "@langchain/langgraph-sdk"

export const FLEET_ASSISTANT_ID = "47c35ef5-979e-5ab8-8dce-a69ded2244ab"
export const FLEET_THREADS_KEY = ["fleet-threads"]

/** A LangGraph client for Fleet as the caller, in the workspace their LangSmith login chose. */
export function fleetClient() {
  return new Client({
    apiUrl: new URL(dashboardApiUrl("/fleet-chat"), window.location.origin)
      .href,
    callerOptions: {
      fetch: (input: RequestInfo | URL, init?: RequestInit) =>
        fetch(input, { ...init, credentials: "include" }),
    },
  })
}
