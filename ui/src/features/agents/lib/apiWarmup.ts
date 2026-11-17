import { agentsLangGraphApiUrl } from "./api"
import { SIDEBAR_PREFS_STORAGE_KEY } from "./sidebarPrefs"
import { SIDEBAR_PAGE_SIZE } from "./queries"

const THREAD_PATH_RE =
  /^\/agents\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i
const AGENTS_HOME_RE = /^\/agents\/?$/

/**
 * Serialized into the document with `toString()`, so it may not reference
 * imports, module scope, or any syntax the build lowers with a helper.
 *
 * The sidebar query is built here rather than passed in: every part of it comes
 * from a localStorage preference that only the browser can read, and a single
 * wrong parameter costs a whole duplicate request.
 */
function warmApiRequests(
  urls: Array<string>,
  sidebarPath: string | null,
  prefsKey: string,
  pageSize: number
) {
  if (document.readyState !== "loading") return

  const targets = urls.slice()
  if (sidebarPath) {
    let includeAutomations = false
    let includeResolved = false
    let owned = true
    let includeClaudeCode = true
    let includeSlack = true
    let repoMode = false
    let sortByActivity = true
    try {
      const raw = localStorage.getItem(prefsKey)
      const prefs = raw ? JSON.parse(raw) : null
      const filters = prefs?.filters
      repoMode = prefs?.organize === "repo" || prefs?.organize === "workspace"
      sortByActivity = !repoMode && prefs?.organize !== "project"
      if (filters) {
        includeAutomations =
          filters.includeAutomations === true ||
          (Array.isArray(filters.sources) &&
            filters.sources.indexOf("schedule") !== -1)
        includeResolved = filters.includeResolved === true
        owned = filters.ownedOnly !== false
        includeClaudeCode = filters.includeClaudeCode !== false
        includeSlack = filters.includeSlack !== false
      }
    } catch {
      // An unreadable preference just means the defaults.
    }
    // The parameters have to be the api client's, though not in its order: the
    // handoff below sorts query keys before matching.
    const search = new URLSearchParams()
    search.set("hierarchy", "true")
    search.set("limit", String(pageSize))
    search.set("offset", "0")
    if (!includeResolved) search.set("resolved", "false")
    search.set("scope", includeAutomations ? "all" : "interactive")
    if (repoMode) search.set("ownerless", "true")
    if (owned) search.set("owned", "true")
    if (!includeClaudeCode) search.append("exclude_source", "claude_code")
    if (!includeSlack) search.append("exclude_source", "slack")
    search.set("sort_by", sortByActivity ? "updated_at" : "created_at")
    targets.push(sidebarPath + "?" + search.toString())
  }

  // A request matches a warmed one whatever order its query keys come in.
  // `sort` is stable, so a repeated key's values keep their order.
  function requestKey(url: string): string {
    const resolved = new URL(url, location.href)
    resolved.searchParams.sort()
    return resolved.href
  }

  const pending = new Map<
    string,
    {
      request: Promise<Response>
      timing: { startedAt?: number; endedAt?: number }
    }
  >()
  for (const url of targets) {
    const href = new URL(url, location.href).href
    const timing: { startedAt?: number; endedAt?: number } = {}
    try {
      timing.startedAt = performance.now()
    } catch {}
    const request = fetch(href, { credentials: "include" })
    request.then(
      () => {
        try {
          timing.endedAt = performance.now()
        } catch {}
      },
      () => {
        try {
          timing.endedAt = performance.now()
        } catch {}
      }
    )
    pending.set(requestKey(href), { request, timing })
  }

  const original = window.fetch
  const timer = setTimeout(release, 15000)

  function release() {
    clearTimeout(timer)
    pending.clear()
    if (window.fetch === patched) window.fetch = original
  }

  function patched(
    input: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> {
    const request = input instanceof Request ? input : null
    const method = String(
      init?.method ?? request?.method ?? "GET"
    ).toUpperCase()
    if (pending.size > 0 && method === "GET") {
      const href =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url
      try {
        const key = requestKey(href)
        const warmed = pending.get(key)
        if (warmed) {
          pending.delete(key)
          if (pending.size === 0) release()
          try {
            return Object.assign(warmed.request, {
              __openSweWarmTiming: warmed.timing,
            })
          } catch {
            return warmed.request
          }
        }
      } catch {
        // A URL the constructor rejects is not one we warmed.
      }
    }
    return original.call(window, input, init)
  }

  window.fetch = patched
}

/**
 * The fields the thread stream's state read selects: the SDK's own thread-state
 * fields, then the messages it keeps under `stateKeys: []`.
 */
const THREAD_STATE_SELECT = [
  "next",
  "tasks",
  "checkpoint",
  "parent_checkpoint",
  "created_at",
  "metadata",
  "values.messages",
]

function threadStateUrl(threadId: string): string {
  const search = new URLSearchParams()
  for (const field of THREAD_STATE_SELECT) search.append("select", field)
  return `${agentsLangGraphApiUrl}/threads/${threadId}/state?${search}`
}

/** The sidebar page endpoint; the script appends the preference-dependent query. */
function sidebarPageEndpoint(): string {
  return `${agentsLangGraphApiUrl}/threads/page`
}

/**
 * Inline head script that starts the requests a route needs while the HTML is
 * still parsing and hands each in-flight response to the app's own later call.
 * Nothing can be requested until the bundle boots, which is most of the delay
 * before either the transcript or the sidebar can paint.
 */
export function apiWarmupScript(pathname: string): string | null {
  const threadId = THREAD_PATH_RE.exec(pathname)?.[1]
  const isAgentsHome = AGENTS_HOME_RE.test(pathname)
  if (!threadId && !isAgentsHome) return null

  const urls = threadId ? [threadStateUrl(threadId)] : []
  const args = [
    JSON.stringify(urls),
    JSON.stringify(sidebarPageEndpoint()),
    JSON.stringify(SIDEBAR_PREFS_STORAGE_KEY),
    JSON.stringify(SIDEBAR_PAGE_SIZE),
  ].join(",")
  return `(${warmApiRequests.toString()})(${args});`
}
