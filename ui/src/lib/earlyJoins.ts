/** Names the join a run stream answers: its path under the API's base, and its query. */
const JOIN_HEADER = "x-robin-join"

/** The `Last-Event-ID` of a page's first join of a run: all of it, after the head. */
const FROM_START = "-1"

/** A thread page's join of a running run: `GET .../threads/{id}/runs/{run}/stream`. */
const JOIN_PATH = /\/threads\/([^/]+)\/runs\/([^/]+)\/stream$/

interface EarlyJoin {
  response: Promise<Response>
  abort: AbortController
}

/**
 * Joins of running threads' runs, started with a thread's reads on a press on its
 * sidebar row. The stream page joins a running run only once its state read gave
 * it the head checkpoint to replay from, a round trip after that read. The backend
 * joins the run as the page would, from the head it reads itself, and names the
 * join, so the page's join takes the early stream when it asks for the same one,
 * and otherwise goes out as before. A join no page takes within
 * {@link EarlyJoins.WAIT_MS} is closed.
 */
export class EarlyJoins {
  static readonly WAIT_MS = 10_000

  readonly #joins = new Map<string, EarlyJoin>()

  /** `url` joins the thread's running run as the backend finds it. */
  start(threadId: string, url: string): void {
    if (this.#joins.has(threadId)) return
    const abort = new AbortController()
    const join: EarlyJoin = {
      abort,
      response: fetch(url, {
        credentials: "include",
        headers: { "Last-Event-ID": FROM_START },
        signal: abort.signal,
      }),
    }
    this.#joins.set(threadId, join)
    const close = () => {
      if (this.#joins.get(threadId) !== join) return
      this.#joins.delete(threadId)
      abort.abort()
    }
    setTimeout(close, EarlyJoins.WAIT_MS)
    // No run going (204) or no stream: the page joins as it would have.
    join.response.then((answer) => {
      if (answer.status !== 200) close()
    }, close)
  }

  /**
   * `fetcher` for a thread page's stream: its join of a running run takes the early
   * one started for the thread when it asks for the same join.
   */
  newReader(fetcher: typeof fetch): typeof fetch {
    return async (input, init) => {
      const href =
        input instanceof Request
          ? input.url
          : input instanceof URL
            ? input.href
            : input
      const url = new URL(href, location.href)
      const [, threadId] = JOIN_PATH.exec(url.pathname) ?? []
      const join = threadId && this.#joins.get(decodeURIComponent(threadId))
      if (!join) return fetcher(input, init)
      this.#joins.delete(decodeURIComponent(threadId))
      const answer = await join.response.catch(() => null)
      if (
        answer?.status === 200 &&
        EarlyJoins.#asksFor(url, init, answer.headers.get(JOIN_HEADER))
      ) {
        const signal = init?.signal
        if (signal?.aborted) join.abort.abort()
        signal?.addEventListener("abort", () => join.abort.abort(), {
          once: true,
        })
        return answer
      }
      join.abort.abort()
      return fetcher(input, init)
    }
  }

  /**
   * Whether the page's join asks for the stream the early join streams: the same
   * run, from the same head and event, with the same modes and namespaces. Its
   * `cancel_on_disconnect` is left out, as the backend never lets a join cancel
   * its run.
   */
  static #asksFor(
    url: URL,
    init: RequestInit | undefined,
    joined: string | null
  ): boolean {
    if (joined == null) return false
    const early = new URL(joined, url)
    return (
      (init?.method ?? "GET").toUpperCase() === "GET" &&
      new Headers(init?.headers).get("Last-Event-ID") === FROM_START &&
      url.pathname.endsWith(early.pathname) &&
      EarlyJoins.#query(url) === EarlyJoins.#query(early)
    )
  }

  static #query(url: URL): string {
    const params = new URLSearchParams(url.searchParams)
    params.delete("cancel_on_disconnect")
    params.sort()
    return params.toString()
  }
}

export const earlyJoins = new EarlyJoins()
