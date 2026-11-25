import type { MessageTimeOf } from "./streamMessagesToUi"

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
 * Stable client-side arrival timestamp for a tool call id. Tool calls carry no
 * time of their own, so we stamp one the first time an id is observed and reuse
 * it thereafter (persisted to survive reloads, since ids are server-assigned and
 * stable). Messages are timed by {@link MessageClock} instead.
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

/**
 * When each message of one thread view was sent. The time the server gives a
 * message wins and is kept, since a later stream update can carry the message
 * without it. A message first seen while a run streams it is timed by its
 * arrival, which is when it was sent; one first read from history without a
 * time stays untimed, because its arrival is only when the page opened.
 */
export class MessageClock {
  private readonly times = new Map<string, string>()
  private readonly untimed = new Set<string>()
  private opened = false

  /**
   * Times one read of a view that opens on messages already sent, such as a
   * running subagent's: its first read is history, so none of it is live. A
   * time `recorded` for a message stands in for one it does not carry.
   */
  reading(
    live: boolean,
    recorded: ReadonlyMap<string, string> = new Map()
  ): MessageTimeOf {
    const history = !this.opened
    this.opened = true
    return (messageId, sentAt) =>
      this.timeOf(
        messageId,
        sentAt ?? recorded.get(messageId),
        live && !history
      )
  }

  timeOf(
    messageId: string,
    sentAt: string | undefined,
    live: boolean
  ): string | undefined {
    if (sentAt) {
      this.times.set(messageId, sentAt)
      return sentAt
    }
    const known = this.times.get(messageId)
    if (known || this.untimed.has(messageId)) return known
    if (!live) {
      this.untimed.add(messageId)
      return undefined
    }
    const arrived = new Date().toISOString()
    this.times.set(messageId, arrived)
    return arrived
  }
}
