import type { PluginHost, PullRequest } from "@openswe/plugin-sdk"
import { describe, expect, it } from "vitest"

import { DesignQa } from "./queries"

const REPOSITORY = "acme/web"

/** A capture key whose time-ordered id sorts by `order`. */
const key = (pull: number, order: number) =>
  `pulls/${pull}/0190${String(order).padStart(4, "0")}-0000-7000-8000-000000000000`

function host(keys: string[], searched: number[]): PluginHost {
  const stored = [...keys].sort().map((name) => ({
    key: name,
    document: { status: "complete" },
    updatedAt: null,
  }))
  return {
    pulls: {
      search: async () => ({
        pulls: searched.map((number) => ({ repository: REPOSITORY, number })),
        incomplete: false,
      }),
    },
    storage: {
      collection: (collection: string) => ({
        query: async ({
          prefix,
          startAfter,
          limit,
        }: {
          prefix: string
          startAfter?: string
          limit: number
        }) =>
          (collection === "captures" ? stored : [])
            .filter(
              ({ key: name }) =>
                name.startsWith(prefix) &&
                (startAfter === undefined || name > startAfter)
            )
            .slice(0, limit),
      }),
    },
    github: () => ({
      pull: async (number: number) =>
        ({ number, state: "open" }) as PullRequest,
    }),
  } as unknown as PluginHost
}

describe("DesignQa.inbox", () => {
  it("keeps uncaptured pull requests and selects the newest capture across pages", async () => {
    // Enough captures of other pull requests to fill more than one page.
    const others = Array.from({ length: 600 }, (_, index) =>
      key(1000 + index, 1)
    )
    const qa = DesignQa.of(
      host([key(7, 1), key(7, 2), key(9, 1), ...others], [7, 8, 9, 1599])
    )

    const inbox = await qa.inbox("review-requested")

    expect(
      inbox.items.map(({ pull, run }) => [pull.number, run?.id ?? null])
    ).toEqual([
      [7, key(7, 2).split("/")[2]],
      [8, null],
      [9, key(9, 1).split("/")[2]],
      [1599, key(1599, 1).split("/")[2]],
    ])
    expect(inbox.incomplete).toBe(false)
  })
})
