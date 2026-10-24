import { dashboardApiBase } from "./api-base"
import { DashboardRequestError } from "./dashboard-fetch"
import { getDatadogRum, subscribeToDatadogInitialization } from "./datadog"

type ErrorContext = Record<string, string | number | null>

const pending: Array<{ error: unknown; context: ErrorContext }> = []
const reported = new WeakMap<object, string>()
const requestIds = new Set<string>()
let transport: typeof fetch | undefined
let installed = false

subscribeToDatadogInitialization(() => {
  for (const { error, context } of pending.splice(0))
    getDatadogRum()?.addError?.(error, context)
})

export function recordClientError({
  title,
  error,
  mutation,
  automaticRum = false,
}: {
  title: string
  error: unknown
  mutation?: string
  automaticRum?: boolean
}): string {
  if (typeof window === "undefined") return ""
  if (
    (error instanceof Error || error instanceof DOMException) &&
    error.name === "AbortError"
  )
    return ""
  const object = error !== null && typeof error === "object" ? error : undefined
  const previous = object && reported.get(object)
  if (previous) return previous
  const id =
    error instanceof DashboardRequestError && error.requestId
      ? error.requestId
      : `err_${crypto.randomUUID()}`
  if (object) reported.set(object, id)
  if (requestIds.has(id)) return id
  requestIds.add(id)
  if (requestIds.size > 1000)
    requestIds.delete(requestIds.values().next().value!)

  const status = error instanceof DashboardRequestError ? error.status : null
  const context = {
    error_id: id,
    error_title: title,
    mutation: mutation ?? null,
    status,
  }
  const rum = getDatadogRum()
  if (rum) {
    if (!automaticRum) rum.addError?.(error, context)
  } else {
    if (pending.length === 100) pending.shift()
    pending.push({ error, context })
  }

  if (typeof window !== "undefined") {
    const send = transport ?? fetch
    void send(`${dashboardApiBase()}/dashboard/api/client-errors`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        error_id: id,
        title: title.slice(0, 200),
        error_message: (error instanceof Error
          ? error.message
          : String(error)
        ).slice(0, 2000),
        status,
        mutation: mutation?.slice(0, 500) ?? null,
        path: window.location.pathname.slice(0, 500),
      }),
    }).then(
      (response) => {
        if (!response.ok)
          console.warn("Could not report client error", response.status)
      },
      (failure: unknown) =>
        console.warn("Could not report client error", failure)
    )
  }
  return id
}

export function installClientErrorReporting(): void {
  if (installed) return
  installed = true
  transport = window.fetch.bind(window)
  window.addEventListener(
    "error",
    (event) => {
      recordClientError({
        title: "Uncaught browser error",
        error:
          event.error ?? new Error(event.message || "Resource failed to load"),
        automaticRum: true,
      })
    },
    true
  )
  window.addEventListener("unhandledrejection", (event) => {
    recordClientError({
      title: "Unhandled promise rejection",
      error: event.reason,
      automaticRum: true,
    })
  })
  const originalConsoleError = console.error.bind(console)
  console.error = (...args: unknown[]) => {
    originalConsoleError(...args)
    recordClientError({
      title: typeof args[0] === "string" ? args[0] : "Browser console error",
      error:
        args.find((arg) => arg instanceof Error) ?? new Error(String(args[0])),
      automaticRum: true,
    })
  }
  const originalConsoleWarn = console.warn.bind(console)
  console.warn = (...args: unknown[]) => {
    originalConsoleWarn(...args)
    const error = args.find((arg) => arg instanceof Error)
    if (error && args[0] !== "Could not report client error")
      recordClientError({ title: String(args[0]), error })
  }
  const originalFetch = transport
  window.fetch = async (input, init) => {
    const url = new URL(
      input instanceof Request ? input.url : String(input),
      window.location.href
    )
    const reporting =
      !url.pathname.includes("/dashboard/api/") ||
      url.pathname.endsWith("/dashboard/api/client-errors")
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined)
    )
    const requestId = headers.get("X-Request-ID") ?? undefined
    try {
      const response = await originalFetch(input, init)
      if (!reporting && !response.ok) {
        recordClientError({
          title: "HTTP request failed",
          error: new DashboardRequestError(
            response.status,
            `HTTP ${response.status}: ${url.origin}${url.pathname}`,
            requestId
          ),
        })
      }
      return response
    } catch (error) {
      if (
        !reporting &&
        !(error instanceof Error && error.name === "AbortError")
      )
        recordClientError({
          title: "Network request failed",
          error: requestId
            ? new DashboardRequestError(
                0,
                "Couldn't reach the server.",
                requestId
              )
            : error,
        })
      throw error
    }
  }
}
