const STORAGE_KEY = "agent-message-timestamps-v1"
const MAX_ENTRIES = 5000
/**
 * How long new stamps wait to be written. Each write serialises the whole
 * store, and opening a thread stamps all its unseen ids in one render, so a
 * write per id blocked that render for hundreds of milliseconds.
 */
const PERSIST_DELAY_MS = 1000

let cache: Map<string, string> | null = null
let persistTimer: ReturnType<typeof setTimeout> | null = null

/** The `[id, timestamp]` pairs a stored value holds; none if it is corrupt. */
function storedEntries(raw: string | null): Array<[string, string]> {
  let parsed: unknown
  try {
    parsed = raw ? JSON.parse(raw) : null
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  return parsed.filter(
    (entry): entry is [string, string] =>
      Array.isArray(entry) &&
      typeof entry[0] === "string" &&
      typeof entry[1] === "string"
  )
}

/** The newest {@link MAX_ENTRIES} stamps, oldest first. */
function newest(entries: Iterable<[string, string]>): Map<string, string> {
  const byTime = [...entries].sort(([, a], [, b]) =>
    a < b ? -1 : a > b ? 1 : 0
  )
  return new Map(byTime.slice(-MAX_ENTRIES))
}

/**
 * Each tab writes its whole map, so take in what another tab wrote; otherwise
 * this tab's next write drops it. The newest stamps of the two survive, and
 * ids both have keep this tab's stamp, which it may already be showing.
 */
function mergeOtherTab(event: StorageEvent): void {
  if (event.key !== STORAGE_KEY || !cache) return
  const both = new Map(storedEntries(event.newValue))
  for (const [id, iso] of cache) both.set(id, iso)
  cache = newest(both)
}

function load(): Map<string, string> {
  if (cache) return cache
  cache = new Map()
  if (typeof window === "undefined") return cache
  let raw: string | null = null
  try {
    raw = window.localStorage.getItem(STORAGE_KEY)
  } catch {
    // Unavailable storage — start empty.
  }
  cache = newest(storedEntries(raw))
  // A reload or a closed tab inside the delay still keeps what was stamped.
  window.addEventListener("pagehide", persist)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") persist()
  })
  window.addEventListener("storage", mergeOtherTab)
  return cache
}

/** Write pending stamps now. */
function persist(): void {
  if (persistTimer === null || !cache) return
  clearTimeout(persistTimer)
  persistTimer = null
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...cache]))
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
 *
 * The newest {@link MAX_ENTRIES} stamps are kept, oldest first, in memory and
 * in storage.
 */
export function messageArrivalTimestamp(messageId: string): string {
  const map = load()
  const existing = map.get(messageId)
  if (existing) return existing
  const iso = new Date().toISOString()
  map.set(messageId, iso)
  if (map.size > MAX_ENTRIES) {
    const oldest = map.keys().next().value
    if (oldest !== undefined) map.delete(oldest)
  }
  if (persistTimer === null && typeof window !== "undefined")
    persistTimer = setTimeout(persist, PERSIST_DELAY_MS)
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
  return Number.isNaN(date.getTime())
    ? undefined
    : hoverTimeFormatter.format(date)
}
