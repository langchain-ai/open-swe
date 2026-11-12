import { expect, it } from "vitest"

import type { OpenPullRequest } from "@/lib/api"
import { canAttemptMerge, mergesLater } from "./status"

const pr = (fields: Partial<OpenPullRequest>) =>
  ({
    draft: false,
    mergeable: true,
    mergeState: "clean",
    ci: "passing",
    reviewRequired: false,
    requiredChecksPassing: true,
    ...fields,
  }) as OpenPullRequest

it.each([
  [{}, false],
  [{ reviewRequired: true }, true],
  [{ requiredChecksPassing: false }, true],
  [{ requiredChecksPassing: null }, true],
  [{ mergeState: "blocked", ci: "pending" as const }, true],
  [{ mergeState: "blocked" }, false],
])("merges %o later: %s", (fields, later) => {
  expect(mergesLater(pr(fields))).toBe(later)
})

it.each([
  [{ reviewRequired: true }, true],
  [{ draft: true }, false],
  [{ mergeable: false }, false],
  [{ mergeState: "dirty" }, false],
])("offers a merge for %o: %s", (fields, offered) => {
  expect(canAttemptMerge(pr(fields))).toBe(offered)
})
