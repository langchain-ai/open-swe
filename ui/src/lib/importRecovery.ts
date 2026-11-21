type Recovery = "recovered" | "blocked"

class ImportRecovery {
  private readonly preloads = new WeakSet<object>()
  private readonly results = new WeakMap<object, Recovery | Promise<Recovery>>()
  private readonly pending = new Map<Error, (result: Recovery) => void>()
  private navigation?: { event: Event; signal: AbortSignal }
  private installed = false

  install() {
    if (this.installed) return
    this.installed = true

    if ("navigation" in window && window.navigation instanceof EventTarget) {
      window.navigation.addEventListener("navigate", (event) => {
        this.navigation = undefined
        if (
          "destination" in event &&
          typeof event.destination === "object" &&
          event.destination !== null &&
          "sameDocument" in event.destination &&
          event.destination.sameDocument === false &&
          "signal" in event &&
          event.signal instanceof AbortSignal
        ) {
          this.navigation = { event, signal: event.signal }
          event.signal.addEventListener("abort", () => this.retryPending(), {
            once: true,
          })
        }
        this.retryPending()
      })
    }

    window.addEventListener("pageshow", () => {
      this.navigation = undefined
      this.retryPending()
    })
    window.addEventListener("vite:preloadError", (event) => {
      const error: unknown = event.payload
      if (!(error instanceof Error)) return
      if (error.message.startsWith("Unable to preload CSS")) {
        event.preventDefault()
        return
      }
      this.preloads.add(error)
    })
    window.addEventListener("unhandledrejection", (event) => {
      this.recover(event.reason)
    })
    window.addEventListener("error", (event) => {
      this.recover(event.error)
    })
  }

  recover(error: unknown): Recovery | Promise<Recovery> | undefined {
    if (!(error instanceof Error)) return
    const preload = this.preloads.has(error)
    if (
      !preload &&
      !/Loading chunk|Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/.test(
        error.message
      )
    )
      return

    const prior = this.results.get(error)
    if (prior) return prior
    const result = this.isNavigating()
      ? new Promise<Recovery>((resolve) => this.pending.set(error, resolve))
      : this.reload(error)
    this.results.set(error, result)
    return result
  }

  private isNavigating() {
    return (
      this.navigation &&
      !this.navigation.event.defaultPrevented &&
      !this.navigation.signal.aborted
    )
  }

  private retryPending() {
    queueMicrotask(() => {
      if (this.isNavigating()) return
      const errors = [...this.pending]
      const first = errors[0]?.[0]
      if (!first) return
      this.pending.clear()
      const result = this.reload(first)
      for (const [error, resolve] of errors) {
        this.results.set(error, result)
        resolve(result)
      }
    })
  }

  private reload(error: Error): Recovery {
    const now = Date.now()
    let state = { count: 0, expire: now + 30_000, message: "" }
    try {
      const stored: unknown = JSON.parse(
        sessionStorage.getItem("robin_import_recovery") ?? "null"
      )
      if (
        typeof stored === "object" &&
        stored !== null &&
        "count" in stored &&
        typeof stored.count === "number" &&
        "expire" in stored &&
        typeof stored.expire === "number" &&
        stored.expire > now
      ) {
        state = {
          count: stored.count,
          expire: stored.expire,
          message:
            "message" in stored && typeof stored.message === "string"
              ? stored.message
              : "",
        }
      }
    } catch (storageError) {
      console.error("Could not read import recovery state", storageError)
    }

    const preload = this.preloads.has(error)
    if (state.count >= 3 || (!preload && state.message === error.message))
      return "blocked"
    try {
      sessionStorage.setItem(
        "robin_import_recovery",
        JSON.stringify({
          count: state.count + 1,
          expire: state.expire,
          message: preload ? state.message : error.message,
        })
      )
    } catch (storageError) {
      console.error("Could not save import recovery state", storageError)
      return "blocked"
    }

    console.error("Reloading after an import failure", error)
    if (preload) window.location.reload()
    else {
      const url = new URL(window.location.href)
      url.searchParams.set("refresh", now.toString())
      window.location.replace(url)
    }
    return "recovered"
  }
}

export const importRecovery = new ImportRecovery()
