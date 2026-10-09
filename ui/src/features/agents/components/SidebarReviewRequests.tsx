import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { Link } from "@tanstack/react-router"

import type { ReviewPageRef } from "@/features/agents/lib/types"
import { SidebarSectionHeader } from "@/features/agents/components/SidebarSectionHeader"
import { sidebarRowClassName } from "@/features/agents/components/SidebarThreadRow"
import {
  noteReviewOpenedFromSidebar,
  reviewPageRoute,
} from "@/features/reviews/lib/reviewEntry"
import { useOpenPullRequests } from "@/features/reviews/lib/useOpenPullRequests"

export function reviewRequestKey({ owner, repo, number }: ReviewPageRef) {
  return `review:${owner}/${repo}#${number}`.toLowerCase()
}

/** Pull requests whose human review is assigned to the user and still pending. */
export function SidebarReviewRequests({
  login,
  activeKeys,
  collapsed,
  compact,
  onToggleCollapsed,
  onNavigate,
}: {
  login: string
  activeKeys: ReadonlyArray<string>
  collapsed: boolean
  compact: boolean
  onToggleCollapsed: () => void
  onNavigate: () => void
}) {
  const assigned = useOpenPullRequests(
    login,
    [],
    "updatedAt",
    "desc",
    "review-assigned"
  )
  const requests = (assigned.data?.pages ?? [])
    .flatMap((page) => page.pullRequests)
    .map((pr) => {
      const [owner = "", repo = ""] = pr.repo.split("/")
      const ref = { owner, repo, number: pr.number }
      return { key: reviewRequestKey(ref), ref, pr }
    })
  if (requests.length === 0) return null

  return (
    <section className="mb-3">
      <SidebarSectionHeader
        label={`Review requests · ${requests.length}`}
        collapsed={collapsed}
        onToggleCollapsed={onToggleCollapsed}
      />
      {!collapsed &&
        requests.map(({ key, ref, pr }) => (
          <div key={key} className="group/row relative mb-0.5">
            <Link
              {...reviewPageRoute(ref)}
              data-sidebar-thread={key}
              title={`${pr.repo}#${pr.number}`}
              onClick={() => {
                noteReviewOpenedFromSidebar(ref)
                onNavigate()
              }}
              className={sidebarRowClassName({
                compact,
                active: activeKeys.includes(key),
                paddingLeft: "pl-2.5",
                archived: false,
              })}
            >
              <span className="min-w-0 flex-1 truncate text-sm">
                {pr.title}
              </span>
              <span className="shrink-0 text-xxs text-tertiary">
                {ref.repo}#{pr.number}
              </span>
              <GitPullRequestIcon
                size={14}
                weight="regular"
                className="shrink-0 text-icon-tertiary"
                aria-label="Review requested"
              />
            </Link>
          </div>
        ))}
    </section>
  )
}
