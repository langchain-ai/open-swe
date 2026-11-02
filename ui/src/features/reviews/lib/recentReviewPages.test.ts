/** @vitest-environment jsdom */

import { beforeEach, expect, it } from "vitest"

import {
  readRecentReviewPages,
  recordRecentReviewPage,
} from "./recentReviewPages"

beforeEach(() => window.localStorage.clear())

it("keeps a bounded, deduplicated history separate for each account", () => {
  for (let number = 1; number <= 51; number++) {
    recordRecentReviewPage("alice", {
      owner: "acme",
      repo: "api",
      number,
      title: `PR ${number}`,
      author: "bob",
      opened_at: new Date().toISOString(),
    })
  }
  const first = readRecentReviewPages("alice")[1]!
  recordRecentReviewPage("ALICE", { ...first, owner: "ACME", title: "Updated" })
  const recent = readRecentReviewPages("alice")
  expect(recent).toHaveLength(50)
  expect(recent[0]).toMatchObject({ number: 50, title: "Updated" })
  expect(recent.filter((page) => page.number === 50)).toHaveLength(1)
  expect(recent.some((page) => page.number === 1)).toBe(false)
  expect(readRecentReviewPages("bob")).toEqual([])
})
