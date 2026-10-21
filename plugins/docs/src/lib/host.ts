import { connect, HostError, type PluginHost } from "@openswe/plugin-sdk"

let pending: Promise<PluginHost> | null = null

/** The dashboard's port, connected once; it only exists in the browser frame. */
export function hostConnection(): Promise<PluginHost> {
  pending ??= connect()
  return pending
}

export function errorMessage(error: unknown): string {
  if (error instanceof HostError) return error.message
  return error instanceof Error ? error.message : String(error)
}
