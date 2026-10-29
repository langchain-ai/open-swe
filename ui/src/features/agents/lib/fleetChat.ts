import { Client } from "@langchain/langgraph-sdk"
export type { Thread } from "@langchain/langgraph-sdk"

export const FLEET_ASSISTANT_ID = "47c35ef5-979e-5ab8-8dce-a69ded2244ab"

export function fleetClient(workspaceId: string) {
  return new Client({ apiUrl: `open-swe://app/fleet-api/${workspaceId}` })
}

export async function fleetWorkspaces() {
  const connected = await window.openSweDesktop?.fleetStatus?.()
  return connected ? [connected] : []
}
