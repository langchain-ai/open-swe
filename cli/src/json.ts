export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export type JsonObject = { [key: string]: JsonValue }

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function stringAt(
  source: Record<string, unknown> | null,
  key: string
): string | null {
  const value = source?.[key]
  return typeof value === "string" ? value : null
}

export function numberAt(
  source: Record<string, unknown> | null,
  key: string
): number | null {
  const value = source?.[key]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

export function booleanAt(
  source: Record<string, unknown> | null,
  key: string
): boolean | null {
  const value = source?.[key]
  return typeof value === "boolean" ? value : null
}

export function arrayAt(
  source: Record<string, unknown> | null,
  key: string
): unknown[] | null {
  const value = source?.[key]
  return Array.isArray(value) ? value : null
}

export function recordAt(
  source: Record<string, unknown> | null,
  key: string
): Record<string, unknown> | null {
  const value = source?.[key]
  return isRecord(value) ? value : null
}

export function stringArrayAt(
  source: Record<string, unknown> | null,
  key: string
): string[] | null {
  const value = arrayAt(source, key)
  if (value === null) return null
  const out: string[] = []
  for (const entry of value) {
    if (typeof entry !== "string") return null
    out.push(entry)
  }
  return out
}

export function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown
  } catch {
    return null
  }
}

export function errorCode(cause: unknown): string | null {
  return isRecord(cause) && typeof cause["code"] === "string"
    ? cause["code"]
    : null
}

export function errorMessage(cause: unknown): string {
  if (cause instanceof Error) return cause.message
  return typeof cause === "string" ? cause : String(cause)
}
