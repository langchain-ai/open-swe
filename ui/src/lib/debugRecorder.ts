const MAX_LOGS = 40
const MAX_ERRORS = 20
const MAX_REQUESTS = 60
const MAX_TEXT = 600

export interface RecordedLog {
  at: number
  level: "warn" | "error"
  text: string
}

export interface RecordedError {
  at: number
  kind: "error" | "unhandledrejection"
  message: string
  source?: string
  stack?: string
}

export interface RecordedRequest {
  at: number
  method: string
  url: string
  status: number | null
  ms: number
  error?: string
}

function ring<T>(max: number) {
  const items: Array<T> = []
  return {
    push(item: T) {
      items.push(item)
      if (items.length > max) items.splice(0, items.length - max)
    },
    all: (): Array<T> => [...items],
  }
}

const logs = ring<RecordedLog>(MAX_LOGS)
const errors = ring<RecordedError>(MAX_ERRORS)
const requests = ring<RecordedRequest>(MAX_REQUESTS)

export const recordedLogs = (): Array<RecordedLog> => logs.all()
export const recordedErrors = (): Array<RecordedError> => errors.all()
export const recordedRequests = (): Array<RecordedRequest> => requests.all()

export function describeUnknown(value: unknown): string {
  if (typeof value === "string") return value
  if (value instanceof Error)
    return value.stack
      ? `${value.name}: ${value.message}\n${value.stack}`
      : `${value.name}: ${value.message}`
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

function joinArgs(args: Array<unknown>): string {
  return args.map(describeUnknown).join(" ").slice(0, MAX_TEXT)
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  const method =
    init?.method ?? (input instanceof Request ? input.method : "GET")
  return method.toUpperCase()
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input)
}

let installed = false

/**
 * Buffer console noise, uncaught errors and fetch traffic so a snapshot taken
 * after something breaks still has the run-up to it.
 */
export function installDebugRecorder(): void {
  if (installed || typeof window === "undefined") return
  installed = true

  for (const level of ["warn", "error"] as const) {
    const original = console[level].bind(console)
    console[level] = (...args: Array<unknown>) => {
      logs.push({ at: Date.now(), level, text: joinArgs(args) })
      original(...args)
    }
  }

  window.addEventListener("error", (event) => {
    errors.push({
      at: Date.now(),
      kind: "error",
      message: event.message || "unknown error",
      source: event.filename
        ? `${event.filename}:${event.lineno}:${event.colno}`
        : undefined,
      stack:
        event.error instanceof Error
          ? event.error.stack?.slice(0, MAX_TEXT)
          : undefined,
    })
  })

  window.addEventListener("unhandledrejection", (event) => {
    const reason: unknown = event.reason
    errors.push({
      at: Date.now(),
      kind: "unhandledrejection",
      message: describeUnknown(reason).slice(0, MAX_TEXT),
      stack:
        reason instanceof Error ? reason.stack?.slice(0, MAX_TEXT) : undefined,
    })
  })

  const originalFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const started = performance.now()
    const entry = { method: requestMethod(input, init), url: requestUrl(input) }
    try {
      const response = await originalFetch(input, init)
      requests.push({
        at: Date.now(),
        ...entry,
        status: response.status,
        ms: Math.round(performance.now() - started),
      })
      return response
    } catch (error) {
      requests.push({
        at: Date.now(),
        ...entry,
        status: null,
        ms: Math.round(performance.now() - started),
        error: describeUnknown(error).slice(0, 200),
      })
      throw error
    }
  }
}
