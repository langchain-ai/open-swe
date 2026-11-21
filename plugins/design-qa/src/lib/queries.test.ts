import {
  HostError,
  type PluginHost,
  type PullRequestSummary,
} from "@openswe/plugin-sdk"
import { describe, expect, it } from "vitest"

import { Limiter } from "./limiter"
import { DesignQa } from "./queries"

const REPOSITORY = "acme/web"

/** A capture key whose time-ordered id sorts by `order`. */
const key = (pull: number, order: number) =>
  `pulls/${pull}/0190${String(order).padStart(4, "0")}-0000-7000-8000-000000000000`

/** A host whose storage holds `documents` by collection, and refuses an empty prefix as the web UI does. */
function host(documents: Record<string, Record<string, object>>): PluginHost {
  return {
    storage: {
      collection: (collection: string) => ({
        delete: async (name: string) => {
          const stored = documents[collection]
          if (!stored || !(name in stored))
            throw new HostError("document not found", 404)
          delete stored[name]
        },
        query: async ({
          prefix,
          startAfter,
          limit,
        }: {
          prefix?: string
          startAfter?: string
          limit: number
        }) => {
          if (prefix === "")
            throw new Error("prefix must be a non-empty string")
          return Object.entries(documents[collection] ?? {})
            .sort(([a], [b]) => (a < b ? -1 : 1))
            .filter(
              ([name]) =>
                name.startsWith(prefix ?? "") &&
                (startAfter === undefined || name > startAfter)
            )
            .slice(0, limit)
            .map(([name, document]) => ({
              key: name,
              document,
              updatedAt: null,
            }))
        },
      }),
    },
  } as unknown as PluginHost
}

describe("DesignQa", () => {
  it("selects each pull request's newest capture across pages", async () => {
    // Enough captures of other pull requests to fill more than one page.
    const captures = Object.fromEntries(
      [
        key(7, 1),
        key(7, 2),
        key(9, 1),
        ...Array.from({ length: 600 }, (_, index) => key(1000 + index, 1)),
      ].map((name) => [name, { status: "complete" }])
    )
    const qa = DesignQa.of(host({ captures }))

    const { runs } = await qa.repositoryCaptures(REPOSITORY)

    expect(runs.get(7)?.id).toBe(key(7, 2).split("/")[2])
    expect(runs.get(9)?.id).toBe(key(9, 1).split("/")[2])
    expect(runs.get(1599)?.id).toBe(key(1599, 1).split("/")[2])
    expect(runs.has(8)).toBe(false)
  })

  it("keeps newer capture attempts and saved evidence visible, but omits skipped and unsuccessful cards, including pins", async () => {
    const created = new Date(Date.now() - 60_000).toISOString()
    const requested = new Date().toISOString()
    const qa = DesignQa.of(
      host({
        captures: {
          [key(11, 1)]: {
            status: "failed",
            error: "Browser unavailable",
            created_at: created,
          },
          [key(12, 1)]: { status: "complete", created_at: created },
          [key(15, 1)]: { status: "failed", created_at: requested },
          [key(16, 1)]: {
            status: "running",
            created_at: requested,
            updated_at: requested,
          },
          [key(17, 1)]: {
            status: "complete",
            created_at: created,
            report: {
              scenarios: [{ before_url: "base.png", after_url: "pr.png" }],
            },
          },
          [key(14, 1)]: {
            status: "complete",
            report: {
              scenarios: [{ before_url: "base.png", after_url: "pr.png" }],
            },
          },
        },
        review_outcomes: Object.fromEntries(
          [
            [7, "failed", "Browser unavailable", requested],
            [9, "skipped", "No visual changes", requested],
            [10, "blocked", "Browser unavailable", requested],
            [11, "queued", "Capture requested", requested],
            [12, "blocked", "Preview pending: preview is deploying", requested],
            [13, "queued", "Capture requested", requested],
            [14, "failed", "Browser unavailable", requested],
            [15, "queued", "Capture requested", created],
            [16, "skipped", "No visual changes", created],
            [17, "skipped", "No visual changes", requested],
          ].map(([number, status, reason, createdAt]) => [
            `pulls/${number}/attempt`,
            {
              id: `attempt-${number}`,
              status,
              reason,
              head_sha: "head",
              created_at: createdAt,
            },
          ])
        ),
      })
    )
    const found = await qa.repositoryCaptures(REPOSITORY)
    const pull: PullRequestSummary = {
      repository: REPOSITORY,
      number: 7,
      title: "Update navigation",
      url: "https://github.com/acme/web/pull/7",
      draft: false,
      author: "alice",
      createdAt: null,
      updatedAt: null,
    }
    for (const pinned of [false, true]) {
      const items = [7, 9, 10, 11, 12, 13, 14, 15, 16, 17].flatMap((number) => {
        const item = DesignQa.item({ ...pull, number }, found, null, pinned)
        return item ? [item] : []
      })
      expect(items.map((item) => item.pull.number)).toEqual([
        11, 12, 13, 14, 16,
      ])
      expect(items[3]?.run?.report?.scenarios[0]?.before_url).toBe("base.png")
    }
  })

  it("reads every pin without a key prefix", async () => {
    const qa = DesignQa.of(
      host({
        pins: {
          "acme/web/7": {
            repository: "acme/web",
            number: 7,
            title: "Fix",
            pinnedAt: "2026-11-19T10:00:00Z",
          },
        },
      })
    )

    expect((await qa.pins()).map(({ number }) => number)).toEqual([7])
  })

  it("unpins the normalized key and treats an already removed pin as unpinned", async () => {
    const qa = DesignQa.of(
      host({
        pins: {
          "acme/web/7": {
            repository: "acme/web",
            number: 7,
            title: "Fix",
            pinnedAt: "2026-11-19T10:00:00Z",
          },
        },
      })
    )
    const pr = { owner: "Acme", repo: "Web", number: 7 }

    await qa.unpin(pr)
    expect(await qa.pins()).toEqual([])
    await expect(qa.unpin(pr)).resolves.toBeUndefined()
  })

  it.each([
    new HostError("installation not found", 404),
    new HostError("document not found", 403),
    new HostError("Storage unavailable", 503),
    new Error("Network unavailable"),
  ])("does not hide an unpin failure: %s", async (error) => {
    const qa = DesignQa.of({
      storage: {
        collection: () => ({
          delete: async () => {
            throw error
          },
        }),
      },
    } as unknown as PluginHost)

    await expect(
      qa.unpin({ owner: "acme", repo: "web", number: 7 })
    ).rejects.toBe(error)
  })

  it("reads hundreds of pull requests a few at a time, and lets a rate limit be retried", async () => {
    let running = 0
    let most = 0
    const qa = DesignQa.of({
      github: () => ({
        pull: async (number: number) => {
          running += 1
          most = Math.max(most, running)
          await new Promise((done) => setTimeout(done, 1))
          running -= 1
          if (number === 429) throw new HostError("Too many requests", 429)
          if (number === 404) throw new HostError("Not found", 404)
          return { number }
        },
      }),
    } as unknown as PluginHost)

    const read = await Promise.all(
      Array.from({ length: 300 }, (_, index) =>
        qa.currentPull("acme/web", index + 1000)
      )
    )

    expect(read.every((pull) => pull !== null)).toBe(true)
    expect(most).toBeLessThanOrEqual(6)
    await expect(qa.currentPull("acme/web", 429)).rejects.toThrow(HostError)
    expect(await qa.currentPull("acme/web", 404)).toBeNull()
  })

  it("frees a limiter slot when a task throws before returning its promise", async () => {
    const limiter = new Limiter(1)
    const failing = () =>
      limiter.run<number>(() => {
        throw new Error("boom")
      })

    await expect(failing()).rejects.toThrow("boom")
    await expect(failing()).rejects.toThrow("boom")
    await expect(limiter.run(async () => 1)).resolves.toBe(1)
  })
})
