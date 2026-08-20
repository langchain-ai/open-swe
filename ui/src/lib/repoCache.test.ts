/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  REPOS_CACHE_MAX_AGE_MS,
  clearCachedRepos,
  readCachedRepos,
  writeCachedRepos,
} from "./repoCache"

const STORAGE_KEY = "open-swe.repos.cache.v1"

const payload = {
  installations: [{ id: 1, account: "acme", account_type: "Organization" }],
  repositories: [
    { full_name: "acme/api", private: true },
    { full_name: "acme/web", private: false },
  ],
}

describe("repoCache", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("round-trips a payload for the same login", () => {
    writeCachedRepos("octocat", payload)
    const cached = readCachedRepos("octocat")
    expect(cached?.payload).toEqual(payload)
    expect(cached?.updatedAt).toBeGreaterThan(0)
  })

  it("rejects a payload cached for a different login", () => {
    writeCachedRepos("octocat", payload)
    expect(readCachedRepos("hubot")).toBeNull()
  })

  it("rejects entries older than the max age", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        login: "octocat",
        updatedAt: Date.now() - REPOS_CACHE_MAX_AGE_MS - 1,
        payload,
      })
    )
    expect(readCachedRepos("octocat")).toBeNull()
  })

  it("drops malformed repositories and unusable entries", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        login: "octocat",
        updatedAt: Date.now(),
        payload: {
          installations: [{ id: "nope" }],
          repositories: [{ full_name: "acme/api" }, { private: true }, 42],
        },
      })
    )
    const cached = readCachedRepos("octocat")
    expect(cached?.payload).toEqual({
      installations: [],
      repositories: [{ full_name: "acme/api", private: false }],
    })
  })

  it("returns null for corrupt json and after clearing", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json")
    expect(readCachedRepos("octocat")).toBeNull()
    writeCachedRepos("octocat", payload)
    clearCachedRepos()
    expect(readCachedRepos("octocat")).toBeNull()
  })
})
