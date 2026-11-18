import { withRequestTiming } from "@/lib/perf/fetchTiming"

/**
 * GETs started before the reader that needs them exists, such as a thread's
 * state read on a press on its sidebar row, which comes about 80 ms before the
 * click that mounts the thread's stream. A read is kept for
 * {@link EarlyReads.FRESH_MS}: an older one may miss a change its reader must
 * see.
 */
export class EarlyReads {
  static readonly FRESH_MS = 3_000

  readonly #reads = new Map<
    string,
    { response: Promise<Response>; startedAt: number }
  >()

  start(url: string): void {
    const key = EarlyReads.#key(url)
    if (this.#reads.has(key)) return
    const read = {
      response: fetch(url, { credentials: "include" }),
      startedAt: performance.now(),
    }
    this.#reads.set(key, read)
    const forget = () => {
      if (this.#reads.get(key) === read) this.#reads.delete(key)
    }
    setTimeout(forget, EarlyReads.FRESH_MS)
    // A failed read is not handed over, so the reader asks again.
    read.response.then((answer) => {
      if (!answer.ok) forget()
    }, forget)
  }

  /**
   * `fetcher` for one new reader: its first GET of each URL takes the read
   * started for that URL, its query keys in any order. A reader reading again
   * always goes out, so it never gets a read older than its last one.
   */
  newReader(fetcher: typeof fetch): typeof fetch {
    const asked = new Set<string>()
    return (input, init) => {
      const url =
        input instanceof Request
          ? input.url
          : input instanceof URL
            ? input.href
            : input
      const method =
        init?.method ?? (input instanceof Request ? input.method : "GET")
      if (method.toUpperCase() === "GET") {
        const key = EarlyReads.#key(url)
        if (!asked.has(key)) {
          asked.add(key)
          const read = this.#reads.get(key)
          this.#reads.delete(key)
          if (read && performance.now() - read.startedAt < EarlyReads.FRESH_MS)
            // Timed like the reader's other reads, from when it asks.
            return withRequestTiming(() => read.response)(input, init)
        }
      }
      return fetcher(input, init)
    }
  }

  static #key(url: string): string {
    const resolved = new URL(url, location.href)
    resolved.searchParams.sort()
    return resolved.href
  }
}

export const earlyReads = new EarlyReads()
