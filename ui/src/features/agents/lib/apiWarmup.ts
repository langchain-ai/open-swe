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
 * `sidebarUrl` is a template because its scope comes from a localStorage
 * preference that only the browser can read.
 */
function warmApiRequests(
  urls: Array<string>,
  sidebarUrl: string | null,
  prefsKey: string
) {
  if (document.readyState !== "loading") return

  const targets = urls.slice()
  if (sidebarUrl) {
    let includeAutomations = false
    try {
      const raw = localStorage.getItem(prefsKey)
      const filters = raw ? JSON.parse(raw).filters : null
      if (filters) {
        includeAutomations =
          filters.includeAutomations === true ||
          (Array.isArray(filters.sources) &&
            filters.sources.indexOf("schedule") !== -1)
      }
    } catch {
      // An unreadable preference just means the default (false).
    }
    targets.push(
      sidebarUrl.replace(
        "__SIDEBAR_SCOPE__",
        includeAutomations ? "all" : "interactive"
      )
    )
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

/** The first sidebar page request, minus the localStorage-dependent scope. */
function sidebarUrlTemplate(): string {
  const search = new URLSearchParams()
  search.set("limit", String(SIDEBAR_PAGE_SIZE))
  search.set("resolved", "false")
  search.set("scope", "__SIDEBAR_SCOPE__")
  return `${agentsLangGraphApiUrl}/threads/page?${search.toString()}`
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
    JSON.stringify(sidebarUrlTemplate()),
    JSON.stringify(SIDEBAR_PREFS_STORAGE_KEY),
  ].join(",")
  return `(${warmApiRequests.toString()})(${args});`
}
