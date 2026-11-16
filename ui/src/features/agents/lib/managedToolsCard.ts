import type { ConnectableServer, NewManagedServer } from "@/lib/api"

/**
 * What a managed tools card offered: a LangSmith login or the services to connect
 * (`connect_managed_tools`), or a server to confirm adding (`add_managed_tools_server`).
 */
export type ManagedToolsCardOffer =
  | { status: "langsmith_required" }
  | { status: "confirmation_required"; server: NewManagedServer }
  | {
      status: "connection_required"
      missing: Array<ConnectableServer>
      /** Only consent pages can be waited on; API keys need a nudge to continue. */
      continuesAutomatically: boolean
    }

const KINDS = new Set<unknown>(["none", "oauth", "secret"])

function proposedServer(item: unknown): NewManagedServer | null {
  if (typeof item !== "object" || item === null) return null
  const { name, url, kind } = item as Record<string, unknown>
  if (typeof name !== "string" || typeof url !== "string" || !KINDS.has(kind))
    return null
  return { name, url, kind: kind as NewManagedServer["kind"] }
}

function offeredServer(item: unknown): ConnectableServer | null {
  if (typeof item !== "object" || item === null) return null
  const { id, name, kind } = item as Record<string, unknown>
  if (typeof id !== "string" || !KINDS.has(kind)) return null
  return {
    id,
    name: typeof name === "string" ? name : id,
    kind: kind as ConnectableServer["kind"],
  }
}

/**
 * Reads the tool's result. Cards from before managed tools dropped the
 * instance tool kit name services by a slug the connect route no longer
 * takes, so they parse to nothing and show as a plain tool call.
 */
export function parseManagedToolsCard(
  output: string | undefined
): ManagedToolsCardOffer | null {
  if (!output) return null
  let value: unknown
  try {
    value = JSON.parse(output)
  } catch {
    return null
  }
  if (typeof value !== "object" || value === null) return null
  const result = value as Record<string, unknown>
  if (result.status === "langsmith_required") return { status: result.status }
  if (result.status === "confirmation_required") {
    const server = proposedServer(result.server)
    return server ? { status: result.status, server } : null
  }
  if (result.status !== "connection_required" || !Array.isArray(result.missing))
    return null
  const missing = result.missing
    .map(offeredServer)
    .filter((server) => server !== null)
  if (missing.length === 0) return null
  return {
    status: "connection_required",
    missing,
    continuesAutomatically:
      typeof result.continues_automatically === "boolean"
        ? result.continues_automatically
        : missing.every((server) => server.kind === "oauth"),
  }
}
