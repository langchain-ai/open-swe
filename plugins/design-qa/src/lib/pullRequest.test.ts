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
  expect(routePath(route)).toBe(
    "/acme/app/12?run=00000000-0000-0000-0000-000000000001"
  )
  expect(parseRoute(routePath(route))).toEqual(route)
  expect(parseRoute("/acme/app/12")).toEqual({ ...route, run: null })
  expect(parseRoute("/")).toEqual({ page: "inbox" })
  expect(parseRoute("/acme/app/pulls")).toEqual({ page: "inbox" })
})
