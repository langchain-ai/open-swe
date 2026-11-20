import { HostError, type PluginHost } from "@openswe/plugin-sdk"
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
