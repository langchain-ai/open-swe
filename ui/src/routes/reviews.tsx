import { Link, Navigate, createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { BugBeetleIcon, FlagIcon, GitPullRequestIcon } from "@phosphor-icons/react";

import type { ReviewSummary } from "@/lib/api";
import { AppShell, SettingsSection } from "@/components/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/reviews")({ component: ReviewsPage });

export function prSizeLabel(additions: number, deletions: number): string {
  const total = additions + deletions;
  if (total <= 50) return "XS";
  if (total <= 200) return "S";
  if (total <= 600) return "M";
  if (total <= 1500) return "L";
  return "XL";
}

function statusBadge(review: ReviewSummary) {
  if (review.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="size-1.5 animate-pulse rounded-full bg-amber-500" />
        Reviewing
      </span>
    );
  }
  if (review.status === "error") {
    return <span className="text-xs text-destructive">Failed</span>;
  }
  return null;
}

function ReviewsPage() {
  const session = useSession();
  const reviews = useQuery({
    queryKey: ["reviews"],
    queryFn: api.listReviews,
    enabled: !!session.data,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "running") ? 5000 : false,
  });

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  return (
    <AppShell
      user={session.data}
      title="PR Reviews"
      description="Pull requests reviewed by Open SWE Review. Click into one for the full analysis."
    >
      <SettingsSection title="Reviewed Pull Requests">
        {reviews.isLoading && (
          <div className="p-4">
            <Skeleton className="h-24 w-full" />
          </div>
        )}
        {reviews.error && (
          <p className="px-4 py-3 text-xs text-destructive">{reviews.error.message}</p>
        )}
        {reviews.data && reviews.data.length === 0 && (
          <p className="px-4 py-3 text-xs text-muted-foreground">
            No reviews yet. Enable repositories under Open SWE Review settings and open a PR.
          </p>
        )}
        <div className="divide-y divide-border">
          {(reviews.data ?? []).map((review) => (
            <Link
              key={review.thread_id}
              to="/reviews/$owner/$repo/$number"
              params={{
                owner: review.owner,
                repo: review.repo,
                number: String(review.number),
              }}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-muted/40"
            >
              <div className="flex min-w-0 items-center gap-3">
                <GitPullRequestIcon className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-foreground">
                    {review.title}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {review.owner}/{review.repo}#{review.number}
                    {review.head_ref && (
                      <span className="ml-2 font-mono text-[11px]">{review.head_ref}</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-3 text-xs">
                {statusBadge(review)}
                <span
                  className={cn(
                    "inline-flex items-center gap-1",
                    review.counts.bugs > 0 ? "text-destructive" : "text-muted-foreground",
                  )}
                >
                  <BugBeetleIcon className="size-3.5" />
                  {review.counts.bugs}
                </span>
                <span className="inline-flex items-center gap-1 text-muted-foreground">
                  <FlagIcon className="size-3.5" />
                  {review.counts.flags}
                </span>
              </div>
            </Link>
          ))}
        </div>
      </SettingsSection>
    </AppShell>
  );
}
