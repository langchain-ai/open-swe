import { connect, HostError, type PluginHost } from "@openswe/plugin-sdk"

// Listen as the module loads: the dashboard hands over its port on the frame's
// load event, which fires before React has mounted anything.
const pending: Promise<PluginHost> | null =
  typeof window === "undefined" ? null : connect()

/** The dashboard's port; it only exists in the browser frame. */
export function hostConnection(): Promise<PluginHost> {
  if (!pending)
    throw new Error("The dashboard is only reachable in the browser")
  return pending
}

export function errorMessage(error: unknown): string {
  if (error instanceof HostError) return error.message
  return error instanceof Error ? error.message : String(error)
}
