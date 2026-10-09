import { CheckCircleIcon } from "@phosphor-icons/react/dist/ssr/CheckCircle"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { ProhibitIcon } from "@phosphor-icons/react/dist/ssr/Prohibit"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"
import type { ReactNode } from "react"

import { usePullRequestStatus } from "@/features/reviews/lib/usePullRequestStatus"
import type { PullRequestReviewer, ReviewUserRef } from "@/lib/api"
import { cn } from "@/lib/utils"

const STATES: Record<
  PullRequestReviewer["state"] | "requested",
  { label: string; icon: ReactNode; className: string }
> = {
  approved: {
    label: "Approved",
    icon: <CheckCircleIcon weight="fill" />,
    className: "text-success-secondary",
  },
  changes_requested: {
    label: "Requested changes",
    icon: <XCircleIcon weight="fill" />,
    className: "text-error-secondary",
  },
  commented: {
    label: "Commented",
    icon: <EyeIcon />,
    className: "text-secondary",
  },
  dismissed: {
    label: "Review dismissed",
    icon: <ProhibitIcon />,
    className: "text-secondary",
  },
  requested: {
    label: "Awaiting review",
    icon: <ClockIcon />,
    className: "text-secondary",
  },
}

/** GitHub's Reviewers box: who reviewed and how, then who still owes a review. */
export function ReviewersSection({
  repo,
  number,
  requested,
}: {
  repo: string
  number: number
  requested: Array<ReviewUserRef>
}) {
  const status = usePullRequestStatus(repo, number)
  const reviewers = status.data?.reviewers ?? []
  const reviewed = new Set(reviewers.map((r) => r.login.toLowerCase()))
  const rows = [
    ...requested
      .filter((person) => !reviewed.has(person.login.toLowerCase()))
      .map((person) => ({
        login: person.login,
        avatarUrl: person.avatar_url ?? null,
        state: "requested" as const,
      })),
    ...reviewers,
  ]
  return (
    <section className="px-3 py-3">
      <h3 className="mb-2 text-xs font-medium">Reviewers</h3>
      {rows.length === 0 ? (
        <p className="text-[11px] text-secondary">None</p>
      ) : (
        <div className="space-y-1">
          {rows.map((row) => {
            const state = STATES[row.state]
            return (
              <div
                key={row.login}
                className="flex items-center gap-2 text-[11px]"
              >
                {row.avatarUrl ? (
                  <img
                    src={row.avatarUrl}
                    alt=""
                    className="size-4 rounded-full"
                  />
                ) : (
                  <span className="size-4 rounded-full bg-surface-level-2" />
                )}
                <span className="min-w-0 flex-1 truncate">{row.login}</span>
                <span
                  title={state.label}
                  aria-label={state.label}
                  className={cn("flex shrink-0", state.className)}
                >
                  {state.icon}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
