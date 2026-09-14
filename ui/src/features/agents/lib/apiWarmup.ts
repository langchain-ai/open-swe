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
    let projectMode = true
    let sortByCreated = true
    try {
      const raw = localStorage.getItem(prefsKey)
      const prefs = raw ? JSON.parse(raw) : null
      const filters = prefs?.filters
      projectMode = prefs?.organize !== "list"
      sortByCreated = prefs?.sortChats !== "updated"
      if (filters) {
        includeAutomations =
          filters.includeAutomations === true ||
          (Array.isArray(filters.sources) &&
            filters.sources.indexOf("schedule") !== -1)
        includeResolved = filters.includeResolved === true
      }
    } catch {
      // An unreadable preference just means the default (false).
    }
    // Parameter order has to match the api client's, because the handoff below
    // matches on the resolved URL.
    const search = new URLSearchParams()
    search.set("limit", String(pageSize))
    search.set("offset", "0")
    if (!includeResolved) search.set("resolved", "false")
    search.set("scope", includeAutomations ? "all" : "interactive")
    if (projectMode) search.set("ownerless", "true")
    search.set("sort_by", sortByCreated ? "created_at" : "updated_at")
    targets.push(sidebarPath + "?" + search.toString())
  }

  const pending = new Map<string, Promise<Response>>()
  for (const url of targets) {
    const href = new URL(url, location.href).href
    const request = fetch(href, { credentials: "include" })
    request.catch(() => {})
    pending.set(href, request)
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
        const resolved = new URL(href, location.href).href
        const warmed = pending.get(resolved)
        if (warmed) {
          pending.delete(resolved)
          if (pending.size === 0) release()
          return warmed
        }
      } catch {
        // A URL the constructor rejects is not one we warmed.
      }
    }
    return original.call(window, input, init)
  }

  window.fetch = patched
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

  const urls = threadId
    ? [`${agentsLangGraphApiUrl}/threads/${threadId}/state`]
    : []
  const args = [
    JSON.stringify(urls),
    JSON.stringify(sidebarPageEndpoint()),
    JSON.stringify(SIDEBAR_PREFS_STORAGE_KEY),
    JSON.stringify(SIDEBAR_PAGE_SIZE),
  ].join(",")
  return `(${warmApiRequests.toString()})(${args});`
}
