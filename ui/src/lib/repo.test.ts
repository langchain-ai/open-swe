import { FuzzySearch } from "./fuzzySearch"
import { describe, expect, it } from "vitest"

import { RepositoryLabels, normalizeRepoFullName } from "./repo"

describe("normalizeRepoFullName", () => {
  it("accepts owner/repo", () => {
    expect(normalizeRepoFullName("langchain-ai/langgraph")).toBe(
      "langchain-ai/langgraph"
    )
  })

  it("accepts github URLs", () => {
    expect(
      normalizeRepoFullName(
        "https://github.com/withmartian/code-review-benchmark"
      )
    ).toBe("withmartian/code-review-benchmark")
  })

  it("rejects invalid input", () => {
    expect(normalizeRepoFullName("not-a-repo")).toBeNull()
  })
})

describe("RepositoryLabels search", () => {
  it("finds typoed repository names without matching unrelated repos", () => {
    const labels = new RepositoryLabels(["langchain-ai/langchainplus"])
    expect(labels.matches("langchain-ai/langchainplus", "langchanplus")).toBe(
      true
    )
    expect(labels.matches("langchain-ai/langchainplus", "zzzzzz")).toBe(false)
  })
})

it("keeps numeric and identifier searches literal", () => {
  const search = new FuzzySearch("59163")
  expect(search.matches("59163")).toBe(true)
  expect(search.matches("59136", "59164", "1234")).toBe(false)
  expect(new FuzzySearch("U123ABC").matches("U123ACB")).toBe(false)
})

it("matches nested filename typos and keeps literal path prefixes", () => {
  expect(new FuzzySearch("widgte").matches("src/components/Widget.tsx")).toBe(
    true
  )
  expect(
    new FuzzySearch("src/components").matches("src/components/Widget.tsx")
  ).toBe(true)
  expect(new FuzzySearch("zzzz").matches("src/components/Widget.tsx")).toBe(
    false
  )
})

it("uses only literal matches for short queries", () => {
  expect(new FuzzySearch("ab").matches("ac")).toBe(false)
  expect(new FuzzySearch("a").matches("cat")).toBe(true)
  expect(new FuzzySearch("ab").matches("cabinet")).toBe(true)
})
