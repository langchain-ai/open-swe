import { Avatar } from "@langchain/macaw-components/Avatar"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import { api } from "@/lib/api"
import { RelativeTimestamp } from "./page/RelativeTimestamp"
import { reviewPageRoute } from "./lib/reviewEntry"

export function RecentReviewPages({ login }: { login: string }) {
  const recent = useQuery({
    queryKey: ["reviews", "recent"],
    queryFn: () => api.recentReviewPages(),
  })
  const items = recent.data?.reviews ?? []
  return (
    <div className="mt-space-4 min-h-0 flex-1 overflow-y-auto rounded-lg border border-default bg-surface-level-1">
      {recent.isLoading && (
        <div className="p-space-4">
          <Skeleton className="h-24 w-full" />
        </div>
      )}
      {recent.error && (
        <p className="px-space-4 py-space-3 text-xs text-error-secondary">
          {recent.error.message}
        </p>
      )}
      {recent.data && items.length === 0 && (
        <p className="px-space-4 py-space-3 text-xs text-secondary">
          Review pages you open show up here.
        </p>
      )}
      <div className="divide-y divide-default">
        {items.map((review) => (
          <Link
            key={`${review.owner}/${review.repo}#${review.number}`}
            {...reviewPageRoute(review)}
            className="flex items-center justify-between gap-space-4 px-space-4 py-space-3 transition-colors hover:bg-surface-level-2-hover"
          >
            <div className="flex min-w-0 items-center gap-space-3">
              <Avatar
                size="sm"
                label={review.author}
                imageUrl={`https://github.com/${review.author}.png?size=48`}
              />
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-primary">
                  {review.title}
                </div>
                <div className="mt-0.5 text-xs text-secondary">
                  {review.owner}/{review.repo}#{review.number}
                  <span className="ml-space-2">
                    by{" "}
                    {review.author.toLowerCase() === login.toLowerCase()
                      ? "you"
                      : review.author}
                  </span>
                </div>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-space-3 text-xs text-secondary">
              <span className="capitalize">{review.state}</span>
              <span>
                Opened <RelativeTimestamp value={review.opened_at} />
              </span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
