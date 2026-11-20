import type { PullRequestRef } from "./pullRequest"

export type InboxScope = "review-requested" | "mine" | "pinned" | "people"

/** An inbox tab and which of its pages, counting from 0. */
export interface InboxRoute {
  page: "inbox"
  scope: InboxScope
  index: number
  /** On the People tab, the GitHub login whose pull requests it lists. */
  person?: string
}

/**
 * Where the app is: its path after the web UI's `/agents/plugins/design-qa`.
 * The inbox tab, its page and the run are path segments, since the path is all
 * the web UI keeps. An inbox path has at most two segments, so it never reads
 * as a pull request's `owner/repo/number`, except the People tab's
 * `/people/<login>/<page>`, which wins over a repository owned by `people`.
 */
export type Route =
  | InboxRoute
  | { page: "pull"; pr: PullRequestRef; run: string | null }

const NAME = /^[\w.-]+$/
const NUMBER = /^[1-9]\d{0,9}$/
const RUN = /^[\w-]{1,100}$/
const SCOPES: Record<InboxScope, string> = {
  "review-requested": "requested",
  mine: "mine",
  pinned: "pinned",
  people: "people",
}
const PAGE = /^[1-9]\d{0,4}$/
const LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/

export function parseRoute(path: string): Route {
  const query = path.indexOf("?")
  const pathname = query === -1 ? path : path.slice(0, query)
  const search = new URLSearchParams(query === -1 ? "" : path.slice(query + 1))
  const segments = pathname.split("/").filter(Boolean)
  const scope = (Object.keys(SCOPES) as InboxScope[]).find(
    (one) => SCOPES[one] === segments[0]
  )
  if (scope === "people") {
    const [, person, page] = segments
    if (
      segments.length <= 3 &&
      (person === undefined || LOGIN.test(person)) &&
      (page === undefined || PAGE.test(page))
    )
      return {
        page: "inbox",
        scope,
        index: page ? Number(page) - 1 : 0,
        ...(person ? { person } : {}),
      }
  }
  const [, page] = segments
  if (
    scope &&
    scope !== "people" &&
    segments.length <= 2 &&
    (page === undefined || PAGE.test(page))
  )
    return { page: "inbox", scope, index: page ? Number(page) - 1 : 0 }
  const [owner, repo, number, runs, id, ...rest] = segments
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
    return { page: "inbox", scope: "review-requested", index: 0 }
  // Earlier links kept the run in `?run=`.
  const run = id ?? search.get("run")
  return {
    page: "pull",
    pr: { owner, repo, number: Number(number) },
    run: run && RUN.test(run) ? run : null,
  }
}

export function routePath(route: Route): string {
  if (route.page === "inbox") {
    const page = route.index > 0 ? `/${route.index + 1}` : ""
    if (route.scope === "people")
      return route.person ? `/people/${route.person}${page}` : "/people"
    return route.scope === "review-requested" && !page
      ? "/"
      : `/${SCOPES[route.scope]}${page}`
  }
  const { owner, repo, number } = route.pr
  const run = route.run ? `/runs/${encodeURIComponent(route.run)}` : ""
  return `/${owner}/${repo}/${number}${run}`
}
