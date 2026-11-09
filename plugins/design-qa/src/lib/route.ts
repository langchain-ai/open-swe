import type { PullRequestRef } from "./pullRequest"

/**
 * Where the app is: its path after the web UI's `/agents/plugins/design-qa`.
 * The run is a path segment, since the path is all the web UI keeps of it.
 */
export type Route =
  | { page: "inbox" }
  | { page: "pull"; pr: PullRequestRef; run: string | null }

const NAME = /^[\w.-]+$/
const NUMBER = /^[1-9]\d{0,9}$/
const RUN = /^[\w-]{1,100}$/

export function parseRoute(path: string): Route {
  const query = path.indexOf("?")
  const pathname = query === -1 ? path : path.slice(0, query)
  const search = new URLSearchParams(query === -1 ? "" : path.slice(query + 1))
  const [owner, repo, number, runs, id, ...rest] = pathname
    .split("/")
    .filter(Boolean)
  if (
    !owner ||
    !repo ||
    !number ||
    rest.length > 0 ||
    (runs !== undefined && (runs !== "runs" || !id)) ||
    !NAME.test(owner) ||
    !NAME.test(repo) ||
    !NUMBER.test(number)
  )
    return { page: "inbox" }
  // Earlier links kept the run in `?run=`.
  const run = id ?? search.get("run")
  return {
    page: "pull",
    pr: { owner, repo, number: Number(number) },
    run: run && RUN.test(run) ? run : null,
  }
}

export function routePath(route: Route): string {
  if (route.page === "inbox") return "/"
  const { owner, repo, number } = route.pr
  const run = route.run ? `/runs/${encodeURIComponent(route.run)}` : ""
  return `/${owner}/${repo}/${number}${run}`
}
