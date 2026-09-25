import { describe, expect, test } from "bun:test"

import { parseGitHubRemote, repoFullName } from "../src/git.ts"

describe("parseGitHubRemote", () => {
  const github = [
    "https://github.com/langchain-ai/open-swe.git",
    "https://github.com/langchain-ai/open-swe",
    "https://github.com/langchain-ai/open-swe/",
    "https://x-access-token:token@github.com/langchain-ai/open-swe.git",
    "http://github.com/langchain-ai/open-swe.git",
    "git@github.com:langchain-ai/open-swe.git",
    "git@github.com:langchain-ai/open-swe",
    "ssh://git@github.com/langchain-ai/open-swe.git",
    "ssh://git@github.com:22/langchain-ai/open-swe.git",
    "git://github.com/langchain-ai/open-swe.git",
    "https://www.github.com/langchain-ai/open-swe.git",
  ]

  for (const remote of github) {
    test(`parses ${remote}`, () => {
      expect(parseGitHubRemote(remote)).toEqual({
        owner: "langchain-ai",
        name: "open-swe",
      })
    })
  }

  const rejected = [
    "",
    "   ",
    "https://gitlab.com/langchain-ai/open-swe.git",
    "git@gitlab.com:langchain-ai/open-swe.git",
    "https://github.com/langchain-ai",
    "https://github.com/langchain-ai/open-swe/extra",
    "/Users/me/checkouts/open-swe",
    "../sibling-checkout",
    "https://github.com//open-swe.git",
  ]

  for (const remote of rejected) {
    test(`rejects ${remote || "(blank)"}`, () => {
      expect(parseGitHubRemote(remote)).toBeNull()
    })
  }

  test("keeps dots inside the repository name", () => {
    expect(parseGitHubRemote("git@github.com:owner/repo.js.git")).toEqual({
      owner: "owner",
      name: "repo.js",
    })
  })

  test("formats the full name the backend parses", () => {
    expect(repoFullName({ owner: "langchain-ai", name: "open-swe" })).toBe(
      "langchain-ai/open-swe"
    )
  })
})
