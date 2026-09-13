import { describe, expect, it } from "vitest"

import { parseGitHubPullRequestUrl } from "./PullRequestPreview"

describe("parseGitHubPullRequestUrl", () => {
  it("parses GitHub pull request links anywhere in text renderers", () => {
    expect(
      parseGitHubPullRequestUrl(
        "https://github.com/langchain-ai/open-swe/pull/123?diff=split#discussion"
      )
    ).toEqual({ repoFullName: "langchain-ai/open-swe", number: 123 })
    expect(
      parseGitHubPullRequestUrl("https://www.github.com/org/repo/pull/7/")
    ).toEqual({ repoFullName: "org/repo", number: 7 })
  })

  it("ignores non-PR and non-GitHub links", () => {
    expect(
      parseGitHubPullRequestUrl("https://github.com/org/repo/issues/123")
    ).toBeNull()
    expect(
      parseGitHubPullRequestUrl("https://example.com/org/repo/pull/123")
    ).toBeNull()
  })
})
