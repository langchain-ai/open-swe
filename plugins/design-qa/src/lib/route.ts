import type { PullRequestRef } from "./pullRequest"

/** Where the app is: its path after the web UI's `/agents/plugins/design-qa`. */
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
  const [owner, repo, number, ...rest] = pathname.split("/").filter(Boolean)
  if (
    !owner ||
    !repo ||
    !number ||
    rest.length > 0 ||
    !NAME.test(owner) ||
    !NAME.test(repo) ||
    !NUMBER.test(number)
  )
    return { page: "inbox" }
  const run = search.get("run")
  return {
    page: "pull",
    pr: { owner, repo, number: Number(number) },
    run: run && RUN.test(run) ? run : null,
  }
}

export function routePath(route: Route): string {
  if (route.page === "inbox") return "/"
  const { owner, repo, number } = route.pr
  const run = route.run ? `?run=${encodeURIComponent(route.run)}` : ""
  return `/${owner}/${repo}/${number}${run}`
}
