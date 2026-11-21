import { resolveDashboardApiBase } from "@/lib/api-base"
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

/** What the page tells the script, which reads everything else where it runs. */
interface WarmupConfig {
  /** `VITE_DASHBOARD_API_BASE_URL` and Vite's `base`, to find the API as the app does. */
  apiBase: string | undefined
  basePath: string
  /** Stands for the thread's id in `thread`, the reads a thread route makes. */
  threadId: string
  thread: typeof THREAD_READS
  /** The reviews page's reads for the pull request its `pr` parameter names. */
  review: Array<string>
  sidebar: string
  /** The session read, or empty where the page already holds the session. */
  session: string
  prefsKey: string
  streamPreferenceKey: string
  streamPreferenceDefault: boolean
  pageSize: number
}

/**
 * Serialized into the document with `toString()`, so it may not reference
 * imports, module scope, or any syntax the build lowers with a helper.
 *
 * The route comes from the address the script runs at, not the page that holds
 * it: the desktop app serves one prerendered document for every route.
 *
 * The sidebar query is built here rather than passed in: every part of it comes
 * from a localStorage preference that only the browser can read, and a single
 * wrong parameter costs a whole duplicate request.
 */
function warmApiRequests(
  config: WarmupConfig,
  threadRoute: RegExp,
  homeRoute: RegExp,
  reviewsRoute: RegExp,
  resolveApiBase: typeof resolveDashboardApiBase
) {
  if (document.readyState !== "loading") return

  const base = config.basePath.replace(/\/$/, "")
  const route =
    base && location.pathname.indexOf(base + "/") === 0
      ? location.pathname.slice(base.length)
      : location.pathname
  const api =
    resolveApiBase(config.apiBase, location.protocol, config.basePath) +
    "/dashboard/api"
  const threadId = threadRoute.exec(route)?.[1]
  const review = reviewsRoute.test(route) ? config.review : []

  const targets: Array<{ url: string; dashboard: boolean }> = []
  if (config.session) {
    targets.push({ url: api + config.session, dashboard: true })
  }
  for (const path of review) targets.push({ url: api + path, dashboard: true })
  if (threadId) {
    const read = (path: string) => api + path.replace(config.threadId, threadId)
    let prefersStream = config.streamPreferenceDefault
    try {
      const stored = localStorage.getItem(config.streamPreferenceKey)
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
        { url: read(config.thread.state), dashboard: false },
        { url: read(config.thread.runningRun), dashboard: false }
      )
    } else {
      targets.push({ url: read(config.thread.transcript), dashboard: true })
    }
    targets.push({ url: read(config.thread.summary), dashboard: true })
  }
  if (threadId || review.length > 0 || homeRoute.test(route)) {
    let includeAutomations = false
    let includeResolved = false
    let owned = true
    let includeSlack = true
    let repoMode = false
    let sortByActivity = true
    try {
      const raw = localStorage.getItem(config.prefsKey)
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
    search.set("limit", String(config.pageSize))
    search.set("offset", "0")
    if (!includeResolved) search.set("resolved", "false")
    search.set("scope", includeAutomations ? "all" : "interactive")
    if (repoMode) search.set("ownerless", "true")
    if (owned) search.set("owned", "true")
    if (!includeSlack) search.append("exclude_source", "slack")
    search.set("sort_by", sortByActivity ? "updated_at" : "created_at")
    targets.push({
      url: api + config.sidebar + "?" + search.toString(),
      dashboard: true,
    })
  }
  if (targets.length === 0) return

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

/**
 * The fields the thread stream's lookups for a running run select. A read
 * started early is handed over only when its URL equals the stream's, so these
 * stay the SDK's fields in the SDK's order.
 */
const RUNNING_RUN_SELECT = ["run_id", "kwargs"]

/** Stands for the thread's id in {@link THREAD_READS}. */
const THREAD_ID = "{threadId}"

/** The reads a thread page makes as it opens, as paths under the dashboard API. */
const THREAD_READS = {
  state: `/threads/${THREAD_ID}/state?${new URLSearchParams(
    THREAD_STATE_SELECT.map((field) => ["select", field])
  )}`,
  /**
   * The stream's first lookup for a running run, which it sends with the state
   * read and joins once both have answered.
   */
  runningRun: `/threads/${THREAD_ID}/runs?${new URLSearchParams([
    ["limit", "1"],
    ["offset", "0"],
    ["status", "running"],
    ...RUNNING_RUN_SELECT.map((field) => ["select", field]),
  ])}`,
  /** What `fetchTranscript` requests: the transcript view's whole thread. */
  transcript: `/threads/${THREAD_ID}/transcript`,
  /** What `agentsApi.getThread` requests. The thread page waits on it to show the thread. */
  summary: `/threads/${THREAD_ID}`,
}

export function threadStateUrl(threadId: string): string {
  return agentsLangGraphApiUrl + THREAD_READS.state.replace(THREAD_ID, threadId)
}

export function threadRunningRunUrl(threadId: string): string {
  return (
    agentsLangGraphApiUrl + THREAD_READS.runningRun.replace(THREAD_ID, threadId)
  )
}

/** The join of a running thread's run a press on its row starts (`EarlyJoins`). */
export function threadRunningJoinUrl(threadId: string): string {
  return `${agentsLangGraphApiUrl}/threads/${threadId}/runs/running/stream`
}

/**
 * Inline head script that starts the requests a route needs while the HTML is
 * still parsing and hands each in-flight response to the app's own later call.
 * Nothing can be requested until the bundle boots, which is most of the delay
 * before a transcript, the sidebar or an open pull request can paint.
 * `selectedPullRequest` is the reviews page's `pr` search parameter as the
 * router parsed it, before any route validated it. `readsSession` is whether
 * the page still has to read the session: everywhere but a server render that
 * already settled it.
 */
export function apiWarmupScript(
  selectedPullRequest?: unknown,
  readsSession = false
): string {
  const review = parsePullRequestSelection(selectedPullRequest)
  // The open pull request's detail and diff, and its chat, whose thread the
  // chat panel waits for before it reads anything else.
  const reviewPath =
    review &&
    `/reviews/${encodeURIComponent(review.owner)}/${encodeURIComponent(review.repo)}/${review.number}`
  const config: WarmupConfig = {
    apiBase: import.meta.env.VITE_DASHBOARD_API_BASE_URL,
    basePath: import.meta.env.BASE_URL,
    threadId: THREAD_ID,
    thread: THREAD_READS,
    review: reviewPath
      ? [reviewPath, `${reviewPath}/diff`, `${reviewPath}/chat`]
      : [],
    sidebar: "/threads/page",
    session: readsSession ? "/me" : "",
    prefsKey: SIDEBAR_PREFS_STORAGE_KEY,
    streamPreferenceKey: STREAM_PREFERENCE_STORAGE_KEY,
    streamPreferenceDefault: STREAM_PREFERENCE_DEFAULT,
    pageSize: SIDEBAR_PAGE_SIZE,
  }
  return `(${warmApiRequests.toString()})(${JSON.stringify(config)},${THREAD_PATH_RE},${AGENTS_HOME_RE},${REVIEWS_PATH_RE},${resolveDashboardApiBase.toString()});`
}
