import { expect, it } from "vitest"

import { parsePullRequestInput } from "./pullRequest"
import { parseRoute, routePath } from "./route"

it("reads GitHub links and owner/repo#number anywhere in the text", () => {
  expect(
    parsePullRequestInput("https://github.com/acme/app/pull/42/files", [])
  ).toEqual({ owner: "acme", repo: "app", number: 42 })
  expect(parsePullRequestInput("see acme/app.js#7 please", [])).toEqual({
    owner: "acme",
    repo: "app.js",
    number: 7,
  })
  expect(parsePullRequestInput("not a PR", ["acme/app"])).toBeNull()
  expect(parsePullRequestInput("acme/app#0", [])).toBeNull()
  expect(
    parsePullRequestInput("https://github.com/acme/app/issues/42", [])
  ).toBeNull()
})

it("opens a bare number only when exactly one repository is shared", () => {
  expect(parsePullRequestInput("#12", ["acme/app"])).toEqual({
    owner: "acme",
    repo: "app",
    number: 12,
  })
  expect(parsePullRequestInput(" 12 ", ["acme/app"])).toEqual({
    owner: "acme",
    repo: "app",
    number: 12,
  })
  expect(parsePullRequestInput("12", [])).toBeNull()
  expect(parsePullRequestInput("12", ["acme/app", "acme/site"])).toBeNull()
})

it("keeps the pull request and selected run in the app's path", () => {
  const route = {
    page: "pull",
    pr: { owner: "acme", repo: "app", number: 12 },
    run: "00000000-0000-0000-0000-000000000001",
  } as const
  // The web UI keeps only the path in its URL, so the run is a path segment.
  expect(routePath(route)).toBe(
    "/acme/app/12/runs/00000000-0000-0000-0000-000000000001"
  )
  expect(parseRoute(routePath(route))).toEqual(route)
  expect(
    parseRoute("/acme/app/12?run=00000000-0000-0000-0000-000000000001")
  ).toEqual(route)
  expect(parseRoute("/acme/app/12")).toEqual({ ...route, run: null })
  const requested = { page: "inbox", scope: "review-requested", index: 0 }
  expect(parseRoute("/acme/app/12/runs")).toEqual(requested)
  expect(parseRoute("/")).toEqual(requested)
  expect(parseRoute("/acme/app/pulls")).toEqual(requested)
})

it("keeps the inbox tab and page in the app's path", () => {
  const paths = {
    "/": { page: "inbox", scope: "review-requested", index: 0 },
    "/requested/3": { page: "inbox", scope: "review-requested", index: 2 },
    "/mine": { page: "inbox", scope: "mine", index: 0 },
    "/mine/2": { page: "inbox", scope: "mine", index: 1 },
  } as const
  for (const [path, route] of Object.entries(paths)) {
    expect(routePath(route)).toBe(path)
    expect(parseRoute(path)).toEqual(route)
  }
  expect(parseRoute("/requested")).toEqual(paths["/"])
  expect(parseRoute("/mine/0")).toEqual(paths["/"])
  expect(parseRoute("/mine/2/3")).toMatchObject({ page: "pull" })
})
