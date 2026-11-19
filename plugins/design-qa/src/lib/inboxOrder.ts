import type { JsonObject, PullRequestSummary } from "@openswe/plugin-sdk"

import type { FrontendReviewRun } from "./capture"

export const PAGE_SIZES = [10, 15, 20, 50, 100] as const

export type InboxSort =
  | "updated"
  | "risk-high"
  | "risk-low"
  | "design-system"
  | "newest"
  | "oldest"

export const SORTS: ReadonlyArray<{ value: InboxSort; label: string }> = [
  { value: "updated", label: "Recently updated" },
  { value: "risk-high", label: "Highest risk" },
  { value: "risk-low", label: "Lowest risk" },
  { value: "design-system", label: "Most design system issues" },
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
]

/** How a person likes the inbox: how many pull requests a page, in what order. */
export interface InboxPreferences {
  pageSize: number
  sort: InboxSort
}

type Run = FrontendReviewRun | undefined

/** The inbox's order, and the preferences that choose it. */
export class InboxOrder {
  static readonly defaults: InboxPreferences = { pageSize: 15, sort: "updated" }

  /** Stored preferences, each field the default when it's missing or unknown. */
  static read(stored: JsonObject | undefined): InboxPreferences {
    const size = stored?.pageSize
    const sort = stored?.sort
    return {
      pageSize:
        PAGE_SIZES.find((one) => one === size) ?? this.defaults.pageSize,
      sort:
        SORTS.find((one) => one.value === sort)?.value ?? this.defaults.sort,
    }
  }

  /**
   * `pulls` in `sort`'s order, by the latest capture `runOf` finds for each;
   * those it can't rank, such as uncaptured ones by risk, follow the rest,
   * most recently updated first, as are ties.
   */
  static sorted<P extends PullRequestSummary>(
    pulls: readonly P[],
    runOf: (pull: P) => Run,
    sort: InboxSort
  ): P[] {
    const time = (value: string | null) => (value ? Date.parse(value) : 0)
    const rank = (pull: P): number | null => {
      const report = runOf(pull)?.report
      if (sort === "risk-high") return report?.risk ? -report.risk.score : null
      if (sort === "risk-low") return report?.risk ? report.risk.score : null
      if (sort === "design-system") {
        const findings = report?.design_system?.findings
        if (!findings) return null
        const errors = findings.filter((f) => f.severity === "error").length
        return -(errors * 1000 + findings.length - errors)
      }
      if (sort === "newest") return -time(pull.createdAt)
      if (sort === "oldest") return time(pull.createdAt)
      return -time(pull.updatedAt)
    }
    return pulls
      .map((pull) => ({ pull, rank: rank(pull) }))
      .sort(
        (a, b) =>
          (a.rank === null ? 1 : 0) - (b.rank === null ? 1 : 0) ||
          (a.rank ?? 0) - (b.rank ?? 0) ||
          time(b.pull.updatedAt) - time(a.pull.updatedAt)
      )
      .map(({ pull }) => pull)
  }
}
