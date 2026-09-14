import { useStreamPool } from "@/features/agents/lib/stream/streamPool"
import { dashboardApiBase } from "@/lib/api-base"
import { getDatadogSessionLink, isDatadogRumInitialized } from "@/lib/datadog"
import {
  describeUnknown,
  recordedErrors,
  recordedLogs,
  recordedRequests,
} from "@/lib/debugRecorder"
import { THEME_STORAGE_KEY } from "@/lib/theme"
import type { QueryClient } from "@tanstack/react-query"
import type { AnyRouter } from "@tanstack/react-router"
import type { SessionUser } from "@/lib/api"
import type {
  RecordedError,
  RecordedLog,
  RecordedRequest,
} from "@/lib/debugRecorder"

const SENSITIVE_PARAM_RE =
  /(token|secret|password|signature|^code$|^state$|^key$|apikey|api[-_]key|auth)/i
const SENSITIVE_STORAGE_RE =
  /(token|secret|password|credential|bearer|cookie|apikey|api[-_]key|authorization)/i
const REDACTED = "[redacted]"
const MAX_VALUE = 400
const MAX_QUERIES = 60

export interface DebugSnapshot {
  meta: {
    schema: number
    trigger: string
    at: string
    atLocal: string
    timeZone: string
    uptimeMs: number
  }
  app: {
    url: string
    referrer: string
    apiBase: string
    basePath: string
    mode: string
    desktop: boolean
    datadog: { initialized: boolean; sessionLink: string | null }
  }
  user: { login: string; email: string | null; isAdmin: boolean } | null
  route: {
    status: string
    isLoading: boolean
    resolvedPathname: string | null
    pathname: string
    search: unknown
    hash: string
    matches: Array<{
      routeId: string
      status: string
      params: unknown
      error: string | null
    }>
  }
  environment: {
    userAgent: string
    platform: string
    language: string
    online: boolean
    viewport: { width: number; height: number; dpr: number }
    screen: { width: number; height: number }
    prefersColorScheme: "dark" | "light"
    theme: string
    visibility: string
    hasFocus: boolean
    connection: Record<string, unknown> | null
    memoryMb: Record<string, number> | null
  }
  dom: {
    activeElement: string
    openDialogs: number
    rootClasses: string
    scrollY: number
  }
  navigationTiming: Record<string, number> | null
  queries: Array<{
    key: string
    status: string
    fetchStatus: string
    isInvalidated: boolean
    updatedAt: string | null
    observers: number
    error: string | null
    data: string
  }>
  mutations: Array<{
    key: string
    status: string
    submittedAt: string | null
    error: string | null
  }>
  streams: {
    activeId: string | null
    binding: unknown
    createdThreadId: string | null
    entries: Array<{
      id: string
      transport: string
      threadId: string | null
      awaitingCreation: boolean
      generation: number
      lastActiveAt: string
      isLoading: boolean | null
      isThreadLoading: boolean | null
      messages: number | null
      interrupts: number | null
      error: string | null
    }>
  }
  requests: Array<RecordedRequest & { atLocal: string }>
  logs: Array<RecordedLog & { atLocal: string }>
  errors: Array<RecordedError & { atLocal: string }>
  storage: { local: Record<string, string>; sessionKeys: Array<string> }
  failures: Array<string>
}

function localTime(at: number): string {
  return new Date(at).toLocaleString(undefined, { hour12: false })
}

function truncate(value: string, max = MAX_VALUE): string {
  return value.length > max ? `${value.slice(0, max)}…(${value.length})` : value
}

function errorText(error: unknown): string | null {
  if (error === null || error === undefined) return null
  return truncate(describeUnknown(error))
}

/** Strip credential-shaped query parameters so a snapshot is safe to paste. */
export function redactUrl(url: string): string {
  try {
    const parsed = new URL(url, "http://localhost")
    let changed = false
    for (const name of Array.from(parsed.searchParams.keys())) {
      if (!SENSITIVE_PARAM_RE.test(name)) continue
      parsed.searchParams.set(name, REDACTED)
      changed = true
    }
    if (!changed) return url
    return url.startsWith("/")
      ? `${parsed.pathname}${parsed.search}`
      : parsed.toString()
  } catch {
    return url
  }
}

export function redactStorage(storage: Storage): Record<string, string> {
  const values: Record<string, string> = {}
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index)
    if (!key) continue
    const value = storage.getItem(key) ?? ""
    values[key] = SENSITIVE_STORAGE_RE.test(key)
      ? REDACTED
      : truncate(value, 200)
  }
  return values
}

function describeData(data: unknown): string {
  if (data === undefined) return "undefined"
  if (data === null) return "null"
  if (Array.isArray(data)) return `Array(${data.length})`
  if (data instanceof Map) return `Map(${data.size})`
  if (typeof data === "object")
    return `{${Object.keys(data).slice(0, 12).join(", ")}}`
  return truncate(String(data), 80)
}

function stringifyKey(key: unknown): string {
  try {
    return truncate(JSON.stringify(key) ?? String(key), 200)
  } catch {
    return String(key)
  }
}

function describeElement(element: Element | null): string {
  if (!element) return "none"
  const id = element.id ? `#${element.id}` : ""
  const classes = element.className
    ? `.${String(element.className).trim().split(/\s+/).slice(0, 3).join(".")}`
    : ""
  return `${element.tagName.toLowerCase()}${id}${classes}`
}

function connectionInfo(): Record<string, unknown> | null {
  const connection = (
    navigator as Navigator & {
      connection?: {
        effectiveType?: string
        downlink?: number
        rtt?: number
        saveData?: boolean
      }
    }
  ).connection
  if (!connection) return null
  return {
    effectiveType: connection.effectiveType,
    downlink: connection.downlink,
    rtt: connection.rtt,
    saveData: connection.saveData,
  }
}

function memoryInfo(): Record<string, number> | null {
  const memory = (
    performance as Performance & {
      memory?: {
        usedJSHeapSize: number
        totalJSHeapSize: number
        jsHeapSizeLimit: number
      }
    }
  ).memory
  if (!memory) return null
  const mb = (bytes: number) => Math.round(bytes / 1024 / 1024)
  return {
    used: mb(memory.usedJSHeapSize),
    total: mb(memory.totalJSHeapSize),
    limit: mb(memory.jsHeapSizeLimit),
  }
}

function navigationTiming(): Record<string, number> | null {
  const [entry] = performance.getEntriesByType(
    "navigation"
  ) as Array<PerformanceNavigationTiming>
  if (!entry) return null
  const round = (value: number) => Math.round(value)
  return {
    ttfbMs: round(entry.responseStart),
    domContentLoadedMs: round(entry.domContentLoadedEventEnd),
    loadMs: round(entry.loadEventEnd),
    transferSizeKb: Math.round(entry.transferSize / 1024),
    resources: performance.getEntriesByType("resource").length,
  }
}

function streamsSnapshot(): DebugSnapshot["streams"] {
  const pool = useStreamPool.getState()
  return {
    activeId: pool.activeId,
    binding: pool.binding,
    createdThreadId: pool.createdThreadId,
    entries: pool.entries.map((entry) => {
      const handle = pool.handles[entry.id]
      return {
        id: entry.id,
        transport: entry.transport,
        threadId: entry.threadId,
        awaitingCreation: entry.awaitingCreation,
        generation: entry.generation,
        lastActiveAt: localTime(entry.lastActiveAt),
        isLoading: handle?.isLoading ?? null,
        isThreadLoading: handle?.isThreadLoading ?? null,
        messages: handle?.messages.length ?? null,
        interrupts: handle?.interrupts.length ?? null,
        error: handle ? errorText(handle.error) : null,
      }
    }),
  }
}

function routeSnapshot(router: AnyRouter): DebugSnapshot["route"] {
  const state = router.state
  return {
    status: state.status,
    isLoading: state.isLoading,
    resolvedPathname: state.resolvedLocation?.pathname ?? null,
    pathname: state.location.pathname,
    search: state.location.search,
    hash: state.location.hash,
    matches: state.matches.map((match) => ({
      routeId: String(match.routeId),
      status: match.status,
      params: match.params,
      error: errorText(match.error),
    })),
  }
}

function queriesSnapshot(queryClient: QueryClient): DebugSnapshot["queries"] {
  return queryClient
    .getQueryCache()
    .getAll()
    .slice(0, MAX_QUERIES)
    .map((query) => ({
      key: stringifyKey(query.queryKey),
      status: query.state.status,
      fetchStatus: query.state.fetchStatus,
      isInvalidated: query.state.isInvalidated,
      updatedAt: query.state.dataUpdatedAt
        ? localTime(query.state.dataUpdatedAt)
        : null,
      observers: query.getObserversCount(),
      error: errorText(query.state.error),
      data: describeData(query.state.data),
    }))
}

function mutationsSnapshot(
  queryClient: QueryClient
): DebugSnapshot["mutations"] {
  return queryClient
    .getMutationCache()
    .getAll()
    .map((mutation) => ({
      key: stringifyKey(mutation.options.mutationKey ?? "anonymous"),
      status: mutation.state.status,
      submittedAt: mutation.state.submittedAt
        ? localTime(mutation.state.submittedAt)
        : null,
      error: errorText(mutation.state.error),
    }))
}

/**
 * Sections are collected independently: a snapshot taken while the page is
 * half-broken should still carry everything that did read cleanly, with the
 * rest named in `failures`.
 */
export function captureDebugSnapshot({
  router,
  queryClient,
  trigger,
}: {
  router: AnyRouter
  queryClient: QueryClient
  trigger: string
}): DebugSnapshot {
  const failures: Array<string> = []
  const section = <T>(name: string, read: () => T, fallback: T): T => {
    try {
      return read()
    } catch (error) {
      failures.push(`${name}: ${describeUnknown(error).slice(0, 200)}`)
      return fallback
    }
  }

  const now = Date.now()
  const user = section<SessionUser | null>(
    "user",
    () => queryClient.getQueryData<SessionUser | null>(["session"]) ?? null,
    null
  )
  const theme = section(
    "theme",
    () => window.localStorage.getItem(THEME_STORAGE_KEY) ?? "system",
    "unknown"
  )

  return {
    meta: {
      schema: 1,
      trigger,
      at: new Date(now).toISOString(),
      atLocal: localTime(now),
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      uptimeMs: Math.round(performance.now()),
    },
    app: {
      url: redactUrl(window.location.href),
      referrer: redactUrl(document.referrer),
      apiBase: dashboardApiBase(),
      basePath: import.meta.env.BASE_URL,
      mode: import.meta.env.MODE,
      desktop: Boolean(window.openSweDesktop),
      datadog: {
        initialized: isDatadogRumInitialized(),
        sessionLink: getDatadogSessionLink() ?? null,
      },
    },
    user: user
      ? { login: user.login, email: user.email, isAdmin: user.is_admin }
      : null,
    route: section("route", () => routeSnapshot(router), {
      status: "unknown",
      isLoading: false,
      resolvedPathname: null,
      pathname: window.location.pathname,
      search: {},
      hash: "",
      matches: [],
    }),
    environment: {
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      language: navigator.language,
      online: navigator.onLine,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        dpr: window.devicePixelRatio,
      },
      screen: { width: window.screen.width, height: window.screen.height },
      prefersColorScheme: window.matchMedia("(prefers-color-scheme: dark)")
        .matches
        ? "dark"
        : "light",
      theme,
      visibility: document.visibilityState,
      hasFocus: document.hasFocus(),
      connection: section("connection", connectionInfo, null),
      memoryMb: section("memory", memoryInfo, null),
    },
    dom: {
      activeElement: describeElement(document.activeElement),
      openDialogs: document.querySelectorAll('[role="dialog"]').length,
      rootClasses: document.documentElement.className,
      scrollY: Math.round(window.scrollY),
    },
    navigationTiming: section("navigationTiming", navigationTiming, null),
    queries: section("queries", () => queriesSnapshot(queryClient), []),
    mutations: section("mutations", () => mutationsSnapshot(queryClient), []),
    streams: section("streams", streamsSnapshot, {
      activeId: null,
      binding: null,
      createdThreadId: null,
      entries: [],
    }),
    requests: recordedRequests().map((entry) => ({
      ...entry,
      url: redactUrl(entry.url),
      atLocal: localTime(entry.at),
    })),
    logs: recordedLogs().map((entry) => ({
      ...entry,
      atLocal: localTime(entry.at),
    })),
    errors: recordedErrors().map((entry) => ({
      ...entry,
      atLocal: localTime(entry.at),
    })),
    storage: section(
      "storage",
      () => ({
        local: redactStorage(window.localStorage),
        sessionKeys: Object.keys(window.sessionStorage),
      }),
      { local: {}, sessionKeys: [] }
    ),
    failures,
  }
}

function requestPath(url: string): string {
  try {
    const parsed = new URL(url, window.location.origin)
    return `${parsed.pathname}${parsed.search}`
  } catch {
    return url
  }
}

function oneLine(value: string, max: number): string {
  return truncate(value.replace(/\s+/g, " ").trim(), max)
}

export function formatDebugSnapshot(snapshot: DebugSnapshot): string {
  const failedRequests = snapshot.requests.filter(
    (request) => request.status === null || request.status >= 400
  )
  const header = [
    "### Open SWE debug snapshot",
    `- when: ${snapshot.meta.atLocal} (${snapshot.meta.timeZone})`,
    `- url: ${snapshot.app.url}`,
    `- route: ${snapshot.route.pathname} (${snapshot.route.status})`,
    `- user: ${snapshot.user ? snapshot.user.login : "signed out"}`,
    `- build: ${snapshot.app.mode}${snapshot.app.desktop ? " desktop" : ""}`,
    `- viewport: ${snapshot.environment.viewport.width}×${snapshot.environment.viewport.height} @${snapshot.environment.viewport.dpr}x, theme ${snapshot.environment.theme}`,
    `- errors: ${snapshot.errors.length}, failed requests: ${failedRequests.length}`,
    snapshot.app.datadog.sessionLink
      ? `- datadog: ${snapshot.app.datadog.sessionLink}`
      : "- datadog: no session",
    ...failedRequests.slice(-3).map((request) => {
      const reason = request.detail ?? request.error ?? ""
      return `- failed: ${request.method} ${requestPath(request.url)} → ${request.status ?? "network"}${reason ? ` ${oneLine(reason, 160)}` : ""}`
    }),
  ]
  return `${header.join("\n")}\n\n\`\`\`json\n${JSON.stringify(snapshot, null, 2)}\n\`\`\`\n`
}
