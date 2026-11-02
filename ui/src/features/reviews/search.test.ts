import { expect, it } from "vitest"
import { parsePullRequestReference, validateReviewsSearch } from "./search"

it("preserves an explicit ascending direction", () => {
  expect(
    validateReviewsSearch({ sort: "updatedAt", direction: "asc" }).direction
  ).toBe("asc")
})

it("restores a shared view and bounds malformed URL state", () => {
  expect(
    validateReviewsSearch({
      tab: "all",
      repo: "acme/app",
      q: "retry",
      status: "Conflicted",
      sort: "updatedAt",
      direction: "desc",
      page: "2",
    })
  ).toEqual({
    tab: "all",
    repo: ["acme/app"],
    q: "retry",
    status: ["Conflicted"],
    sort: "updatedAt",
    direction: "desc",
    page: 2,
  })
  expect(
    validateReviewsSearch({
      tab: "bad",
      repo: {},
      q: [],
      status: "bad",
      sort: "bad",
      direction: "bad",
      page: -1,
    })
  ).toEqual({
    tab: undefined,
    repo: undefined,
    q: undefined,
    status: undefined,
    sort: undefined,
    direction: undefined,
    page: undefined,
  })
})

it("restores multiple repositories and statuses, dropping invalid and duplicate values", () => {
  const result = validateReviewsSearch({
    repo: ["acme/app", "acme/other", "acme/app", null],
    status: ["Failing", "Conflicted", "Failing", "invalid"],
  })
  expect(result.repo).toEqual(["acme/app", "acme/other"])
  expect(result.status).toEqual(["Conflicted", "Failing"])
})

it("restores every expanded file in a large PR without truncating the saved state", () => {
  const files = Array.from(
    { length: 150 },
    (_, index) => `src/file-${index}.ts`
  )
  const saved = JSON.parse(JSON.stringify({ files }))
  expect(validateReviewsSearch(saved).files).toEqual(files)
})

it("finds the pull request in a pasted link with surrounding text", () => {
  const expected = { owner: "langchain-ai", repo: "open-swe", number: 3178 }
  expect(
    parsePullRequestReference(
      "see https://github.com/langchain-ai/open-swe/pull/3178/files?w=1#diff thanks"
    )
  ).toEqual(expected)
  expect(parsePullRequestReference("langchain-ai/open-swe#3178")).toEqual(
    expected
  )
  expect(
    parsePullRequestReference("https://github.com/langchain-ai")
  ).toBeNull()
})

it("keeps compact expansion overrides separate from legacy expanded paths", () => {
  expect(validateReviewsSearch({ folds: [] }).folds).toEqual([])
  expect(
    validateReviewsSearch({ folds: ["src/one.ts", "src/one.ts", null, ""] })
      .folds
  ).toEqual(["src/one.ts"])
  expect(validateReviewsSearch({ files: ["src/legacy.ts"] }).files).toEqual([
    "src/legacy.ts",
  ])
})
