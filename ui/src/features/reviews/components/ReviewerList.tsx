import { CheckCircleFillIcon } from "@langchain/macaw-components/icons"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { ProhibitIcon } from "@phosphor-icons/react/dist/ssr/Prohibit"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"
import type { ReactNode } from "react"

import type { PullRequestReviewer, ReviewUserRef } from "@/lib/api"
import { cn } from "@/lib/utils"
import { sameLogin } from "@/features/reviews/lib/logins"
import { ProfileLink } from "@/features/reviews/page/notes/Byline"

type ReviewerState = PullRequestReviewer["state"] | "requested"

const STATES: Record<
  ReviewerState,
  { label: string; icon: ReactNode; className: string }
> = {
  approved: {
    label: "Approved",
    icon: <CheckCircleFillIcon aria-hidden size={14} />,
    className: "text-icon-success",
  },
  changes_requested: {
    label: "Requested changes",
    icon: <XCircleIcon aria-hidden size={14} weight="fill" />,
    className: "text-icon-error",
  },
  commented: {
    label: "Commented",
    icon: <EyeIcon aria-hidden size={14} weight="regular" />,
    className: "text-icon-secondary",
  },
  dismissed: {
    label: "Review dismissed",
    icon: <ProhibitIcon aria-hidden size={14} weight="regular" />,
    className: "text-icon-secondary",
  },
  requested: {
    label: "Awaiting review",
    icon: <ClockIcon aria-hidden size={14} weight="regular" />,
    className: "text-icon-warning",
  },
}

/**
 * GitHub's Reviewers box: who still owes a review, then who reviewed and how.
 * The author answering in threads is not reviewing, so they never appear.
 */
export function reviewerRows(
  reviewers: ReadonlyArray<PullRequestReviewer>,
  requested: ReadonlyArray<ReviewUserRef>,
  author: string | undefined
): Array<{ login: string; state: ReviewerState }> {
  const reviewed = (login: string) =>
    reviewers.some((reviewer) => sameLogin(reviewer.login, login))
  return [
    ...requested
      .filter((person) => !reviewed(person.login))
      .map((person) => ({ login: person.login, state: "requested" as const })),
    ...reviewers.filter((reviewer) => !sameLogin(reviewer.login, author)),
  ]
}

export function ReviewerList({
  rows,
  className,
}: {
  rows: ReturnType<typeof reviewerRows>
  className?: string
}) {
  return (
    <ul className={cn("flex flex-col gap-space-1 text-xs", className)}>
      {rows.map((row) => {
        const state = STATES[row.state]
        return (
          <li key={row.login} className="flex items-center gap-space-2">
            <span
              title={state.label}
              className={cn("flex shrink-0", state.className)}
            >
              {state.icon}
            </span>
            <ProfileLink author={row} className="font-medium" />
            <span className="text-secondary">{state.label.toLowerCase()}</span>
          </li>
        )
      })}
    </ul>
  )
}
