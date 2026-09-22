import { describe, expect, it } from "vitest"

import { mostRecentRepository, prioritizeRepositories } from "./repositoryUsage"

const usage = (repo: string, use_count: number, day: number) => ({
  repo,
  use_count,
  last_used_at: `2026-06-${String(day).padStart(2, "0")}T00:00:00Z`,
})

describe("prioritizeRepositories", () => {
  it("promotes only four available favorites by frequency and then recency", () => {
    const repos = ["a", "b", "c", "d", "e", "f", "g"]
    expect(
      prioritizeRepositories(
        repos,
        [
          usage("missing", 100, 1),
          usage("g", 5, 1),
          usage("f", 4, 1),
          usage("e", 4, 2),
          usage("D", 3, 1),
          usage("c", 2, 1),
        ],
        (repo) => repo
      )
    ).toEqual(["g", "e", "f", "d", "a", "b", "c"])
    expect(repos).toEqual(["a", "b", "c", "d", "e", "f", "g"])
  })

  it("preserves the supplied ordering without history", () => {
    expect(prioritizeRepositories(["z", "a"], [], (repo) => repo)).toEqual([
      "z",
      "a",
    ])
  })
})

describe("mostRecentRepository", () => {
  it("selects the most recent accessible repository, not the most frequent", () => {
    expect(
      mostRecentRepository(
        [{ full_name: "Acme/Recent" }, { full_name: "acme/frequent" }],
        [
          usage("missing", 1, 20),
          usage("acme/frequent", 20, 1),
          usage("acme/recent", 1, 10),
        ]
      )
    ).toBe("Acme/Recent")
  })

  it("does not restore inaccessible repositories", () => {
    expect(mostRecentRepository([], [usage("missing", 1, 20)])).toBeNull()
  })
})
