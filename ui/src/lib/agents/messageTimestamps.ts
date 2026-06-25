const STORAGE_KEY = "agent-message-timestamps-v1"
const MAX_ENTRIES = 5000

let cache: Map<string, string> | null = null

function load(): Map<string, string> {
  if (cache) return cache
  cache = new Map()
  if (typeof window === "undefined") return cache
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : null
    if (Array.isArray(parsed)) {
      for (const entry of parsed) {
        if (
          Array.isArray(entry) &&
          typeof entry[0] === "string" &&
          typeof entry[1] === "string"
        ) {
          cache.set(entry[0], entry[1])
        }
      }
    }
  } catch {
    // Corrupt/unavailable storage — start empty.
  }
  return cache
}

function persist(map: Map<string, string>): void {
  if (typeof window === "undefined") return
  try {
    const entries = [...map.entries()]
    const trimmed =
      entries.length > MAX_ENTRIES ? entries.slice(-MAX_ENTRIES) : entries
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed))
  } catch {
    // Quota/serialization failure — timestamps stay in memory only.
  }
}

/**
 * Stable client-side arrival timestamp for a message id. The LangGraph messages
 * we render carry no per-message creation time, so we stamp one the first time a
 * message id is observed and reuse it thereafter (persisted to survive reloads,
 * since message ids are server-assigned and stable). Real backend timestamps
 * (`created_at` / `response_metadata.created_at`) take precedence upstream.
 */
export function messageArrivalTimestamp(messageId: string): string {
  const map = load()
  const existing = map.get(messageId)
  if (existing) return existing
  const iso = new Date().toISOString()
  map.set(messageId, iso)
  persist(map)
  return iso
}

const hoverTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
})

/** Format a timestamp for a native hover tooltip (`title`). */
export function formatHoverTimestamp(value?: string): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : hoverTimeFormatter.format(date)
}
