import type { ManagedToolsMissingCredential } from "@/lib/api"

/** What `connect_managed_tools` offered: a LangSmith login, or the services to connect. */
export type ManagedToolsCardOffer =
  | { status: "langsmith_required" }
  | {
      status: "connection_required"
      gateway: { id: string; name: string }
      missing: Array<ManagedToolsMissingCredential>
    }

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
  const gateway = result.gateway as Record<string, unknown> | undefined
  if (
    result.status !== "connection_required" ||
    typeof gateway?.id !== "string" ||
    !Array.isArray(result.missing)
  ) {
    return null
  }
  return {
    status: "connection_required",
    gateway: { id: gateway.id, name: String(gateway.name ?? gateway.id) },
    missing: result.missing as Array<ManagedToolsMissingCredential>,
  }
}
