import { agentsLangGraphApiUrl } from "./api"

const THREAD_PATH_RE =
  /^\/agents\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i

/**
 * Serialized into the document with `toString()`, so it may not reference
 * imports, module scope, or any syntax the build lowers with a helper.
 */
function warmThreadState(stateUrl: string) {
  if (document.readyState !== "loading") return

  const target = new URL(stateUrl, location.href).href
  let pending: Promise<Response> | null = fetch(target, {
    credentials: "include",
  })
  pending.catch(() => {})

  const original = window.fetch
  const timer = setTimeout(release, 15000)

  function release() {
    clearTimeout(timer)
    pending = null
    if (window.fetch === patched) window.fetch = original
  }

  function patched(
    input: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> {
    const method = String(
      init?.method ?? (input as Request).method ?? "GET"
    ).toUpperCase()
    if (pending && method === "GET") {
      const href =
        typeof input === "string"
          ? input
          : ((input as Request).url ?? String(input))
      try {
        if (new URL(href, location.href).href === target) {
          const warmed = pending
          release()
          return warmed
        }
      } catch {
        // A URL the constructor rejects is not the one we warmed.
      }
    }
    return original.call(window, input, init)
  }

  window.fetch = patched
}

/**
 * Inline head script that starts the thread's `getState` request while the HTML
 * is still parsing and hands the in-flight response to the SDK. The SDK's own
 * call cannot be issued until the bundle has booted, which is most of the delay
 * before a transcript can paint.
 */
export function threadStateWarmupScript(pathname: string): string | null {
  const threadId = THREAD_PATH_RE.exec(pathname)?.[1]
  if (!threadId) return null
  const url = `${agentsLangGraphApiUrl}/threads/${threadId}/state`
  return `(${warmThreadState.toString()})(${JSON.stringify(url)});`
}
