import {
  STREAM_PREFERENCE_DEFAULT,
  STREAM_PREFERENCE_STORAGE_KEY,
} from "@/lib/streamPreference"
import { parsePullRequestSelection } from "@/features/reviews/search"
import { agentsLangGraphApiUrl } from "./api"
import { SIDEBAR_PREFS_STORAGE_KEY } from "./sidebarPrefs"
import { SIDEBAR_PAGE_SIZE } from "./queries"

const THREAD_PATH_RE =
  /^\/agents\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i
const AGENTS_HOME_RE = /^\/agents\/?$/
const REVIEWS_PATH_RE = /^\/agents\/reviews\/?$/

/**
 * Serialized into the document with `toString()`, so it may not reference
 * imports, module scope, or any syntax the build lowers with a helper.
 *
 * The sidebar query is built here rather than passed in: every part of it comes
 * from a localStorage preference that only the browser can read, and a single
 * wrong parameter costs a whole duplicate request.
 */
function warmApiRequests(
  thread: {
    state: string
    runningRun: string
    transcript: string
    summary: string
  } | null,
  sidebarPath: string | null,
  prefsKey: string,
  streamPreferenceKey: string,
  streamPreferenceDefault: boolean,
  pageSize: number,
  reviewReads: Array<string>
) {
  if (document.readyState !== "loading") return

  const targets: Array<{ url: string; dashboard: boolean }> = []
  for (const url of reviewReads) targets.push({ url, dashboard: true })
  if (thread) {
    let prefersStream = streamPreferenceDefault
    try {
      const stored = localStorage.getItem(streamPreferenceKey)
      if (stored !== null) prefersStream = stored === "true"
    } catch {
      // An unreadable preference just means the default.
    }
    // For a thread the transcript log records, the setting picks the view, and
    // each view reads only its own source. A thread the log doesn't record opens
    // in the stream view either way, and its state read waits for the summary.
    // That is on purpose: warming the state too would waste it on every other thread.
    if (prefersStream) {
      targets.push(
        { url: thread.state, dashboard: false },
        { url: thread.runningRun, dashboard: false }
      )
    } else {
      targets.push({ url: thread.transcript, dashboard: true })
    }
    targets.push({ url: thread.summary, dashboard: true })
  }
  if (sidebarPath) {
    let includeAutomations = false
    let includeResolved = false
    let owned = true
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
    if (!includeSlack) search.append("exclude_source", "slack")
    search.set("sort_by", sortByActivity ? "updated_at" : "created_at")
    targets.push({
      url: sidebarPath + "?" + search.toString(),
      dashboard: true,
    })
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
  for (const target of targets) {
    const href = new URL(target.url, location.href).href
    const timing: { startedAt?: number; endedAt?: number } = {}
    try {
      timing.startedAt = performance.now()
    } catch {}
    const init: RequestInit = { credentials: "include" }
    if (target.dashboard) {
      // What `agentsRequest` sends. The server tags its trace with the request id.
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      }
      try {
        headers["X-Request-ID"] = "req_" + crypto.randomUUID()
      } catch {}
      init.headers = headers
    }
    const request = fetch(href, init)
    const key = requestKey(href)
    const warmed = { request, timing }
    // A failure that settles before the app asks may be stale by then, such as
    // a new thread's transcript before its first run records it, so the app asks again.
    const settle = (ok: boolean) => {
      try {
        timing.endedAt = performance.now()
      } catch {}
      if (ok || pending.get(key) !== warmed) return
      pending.delete(key)
      if (pending.size === 0) release()
    }
    request.then(
      (response) => settle(response.ok),
      () => settle(false)
    )
    pending.set(key, warmed)
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

export function threadStateUrl(threadId: string): string {
  const search = new URLSearchParams()
  for (const field of THREAD_STATE_SELECT) search.append("select", field)
  return `${agentsLangGraphApiUrl}/threads/${threadId}/state?${search}`
}

/**
 * The stream's first lookup for a running run, which it sends with the state
 * read and joins once both have answered.
 */
export function threadRunningRunUrl(threadId: string): string {
  const search = new URLSearchParams({
    limit: "1",
    offset: "0",
    status: "running",
  })
  return `${agentsLangGraphApiUrl}/threads/${threadId}/runs?${search}`
}

/** What `agentsApi.getThread` requests. The thread page waits on it to show the thread. */
function threadSummaryUrl(threadId: string): string {
  return `${agentsLangGraphApiUrl}/threads/${threadId}`
}

/** What `fetchTranscript` requests: the transcript view's whole thread. */
function threadTranscriptUrl(threadId: string): string {
  return `${agentsLangGraphApiUrl}/threads/${threadId}/transcript`
}

/** The sidebar page endpoint; the script appends the preference-dependent query. */
function sidebarPageEndpoint(): string {
  return `${agentsLangGraphApiUrl}/threads/page`
}

/**
 * Inline head script that starts the requests a route needs while the HTML is
 * still parsing and hands each in-flight response to the app's own later call.
 * Nothing can be requested until the bundle boots, which is most of the delay
 * before a transcript, the sidebar or an open pull request can paint.
 * `selectedPullRequest` is the reviews page's `pr` search parameter as the
 * router parsed it, before any route validated it.
 */
export function apiWarmupScript(
  pathname: string,
  selectedPullRequest?: unknown
): string | null {
  const threadId = THREAD_PATH_RE.exec(pathname)?.[1]
  const isAgentsHome = AGENTS_HOME_RE.test(pathname)
  const review = REVIEWS_PATH_RE.test(pathname)
    ? parsePullRequestSelection(selectedPullRequest)
    : null
  if (!threadId && !isAgentsHome && !review) return null
  // The open pull request's detail and diff, and its chat, whose thread the
  // chat panel waits for before it reads anything else.
  const reviewPath =
    review &&
    `${agentsLangGraphApiUrl}/reviews/${encodeURIComponent(review.owner)}/${encodeURIComponent(review.repo)}/${review.number}`

  const args = [
    JSON.stringify(
      threadId
        ? {
            state: threadStateUrl(threadId),
            runningRun: threadRunningRunUrl(threadId),
            transcript: threadTranscriptUrl(threadId),
            summary: threadSummaryUrl(threadId),
          }
        : null
    ),
    JSON.stringify(sidebarPageEndpoint()),
    JSON.stringify(SIDEBAR_PREFS_STORAGE_KEY),
    JSON.stringify(STREAM_PREFERENCE_STORAGE_KEY),
    JSON.stringify(STREAM_PREFERENCE_DEFAULT),
    JSON.stringify(SIDEBAR_PAGE_SIZE),
    JSON.stringify(
      reviewPath ? [reviewPath, `${reviewPath}/diff`, `${reviewPath}/chat`] : []
    ),
  ].join(",")
  return `(${warmApiRequests.toString()})(${args});`
}
