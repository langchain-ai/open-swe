import { beforeEach, describe, expect, it } from "vitest"

import {
  BROWSER_HISTORY_MAX_ENTRIES_PER_SCOPE,
  migratePersistedBrowserHistory,
  useBrowserHistoryStore,
} from "@/features/agents/browser/browserHistoryStore"

describe("browser history store", () => {
  beforeEach(() => {
    useBrowserHistoryStore.setState({ byScope: {} })
  })

  it("records visits most recent first and merges loopback spellings", () => {
    const { recordVisit } = useBrowserHistoryStore.getState()
    recordVisit("repo:a", "localhost:5173", 100)
    recordVisit("repo:a", "http://127.0.0.1:5173/", 200)
    recordVisit("repo:a", "example.com", 150)
    const entries = useBrowserHistoryStore.getState().byScope["repo:a"] ?? []
    expect(entries.map((entry) => entry.url)).toEqual([
      "http://127.0.0.1:5173/",
      "https://example.com/",
    ])
    expect(entries[0]?.lastVisitedAt).toBe(200)
  })

  it("caps entries per scope and ignores unusable urls", () => {
    const { recordVisit } = useBrowserHistoryStore.getState()
    for (let index = 0; index < 60; index += 1) {
      recordVisit("repo:a", `https://example.com/${index}`, index + 1)
    }
    expect(useBrowserHistoryStore.getState().byScope["repo:a"]).toHaveLength(
      BROWSER_HISTORY_MAX_ENTRIES_PER_SCOPE
    )
    recordVisit("repo:b", "javascript:alert(1)")
    expect(useBrowserHistoryStore.getState().byScope["repo:b"]).toBeUndefined()
  })

  it("titles and removes entries by canonical url", () => {
    const { recordVisit, setTitle, removeUrl } =
      useBrowserHistoryStore.getState()
    recordVisit("repo:a", "http://localhost:3000/docs/", 1)
    setTitle("repo:a", "http://localhost:3000/docs", "  Docs  ")
    expect(
      useBrowserHistoryStore.getState().byScope["repo:a"]?.[0]?.title
    ).toBe("Docs")
    removeUrl("repo:a", "http://127.0.0.1:3000/docs")
    expect(useBrowserHistoryStore.getState().byScope["repo:a"]).toBeUndefined()
  })
})

describe("migratePersistedBrowserHistory", () => {
  it("re-validates every entry and drops credentials", () => {
    const migrated = migratePersistedBrowserHistory({
      byScope: {
        "repo:a": [
          {
            url: "http://user:pw@localhost:3000/",
            lastVisitedAt: 5,
            title: "Home",
          },
          { url: "ftp://nope", lastVisitedAt: 4 },
          { url: "https://example.com", lastVisitedAt: "yesterday" },
          { url: "https://example.com", lastVisitedAt: 9 },
          { url: "https://example.com/", lastVisitedAt: 3 },
        ],
        "": [{ url: "https://example.com", lastVisitedAt: 1 }],
      },
    })
    expect(migrated.byScope["repo:a"]).toEqual([
      { url: "https://example.com/", lastVisitedAt: 9 },
      { url: "http://localhost:3000/", lastVisitedAt: 5, title: "Home" },
    ])
    expect(Object.keys(migrated.byScope)).toEqual(["repo:a"])
    expect(migratePersistedBrowserHistory(null)).toEqual({ byScope: {} })
  })
})
