import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import type { ReposPayload } from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ApiError, api } from "@/lib/api";
import { RequireLogin } from "@/lib/auth-redirect";
import { useSession } from "@/lib/session";

const PAGE_SIZE = 20;

export const Route = createFileRoute("/review_/repositories/$owner")({
  component: RepositoriesOwnerPage,
});

function RepositoriesOwnerPage() {
  const session = useSession();
  const { owner } = Route.useParams();
  const qc = useQueryClient();

  const repos = useQuery<ReposPayload>({
    queryKey: ["repos"],
    queryFn: async () => {
      try {
        return await api.repos();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401)
          return { installations: [], repositories: [] };
        throw e;
      }
    },
    enabled: !!session.data,
  });

  const autoReview = useQuery({
    queryKey: ["autoReviewRepos"],
    queryFn: api.listAutoReviewRepos,
    enabled: !!session.data,
  });

  const toggleAutoReview = useMutation({
    mutationFn: ({ full_name, on }: { full_name: string; on: boolean }) =>
      api.setAutoReviewRepo(full_name, on),
    onSuccess: (data) => {
      qc.setQueryData(["autoReviewRepos"], data);
    },
  });

  const ownerRepos = useMemo(
    () =>
      (repos.data?.repositories ?? [])
        .filter((r) => r.full_name.split("/")[0] === owner)
        .sort((a, b) => a.full_name.localeCompare(b.full_name)),
    [repos.data?.repositories, owner],
  );

  const autoReviewSet = useMemo(
    () => new Set(autoReview.data?.repos ?? []),
    [autoReview.data?.repos],
  );

  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [owner]);

  const totalPages = Math.max(1, Math.ceil(ownerRepos.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageStart = safePage * PAGE_SIZE;
  const pageEnd = Math.min(pageStart + PAGE_SIZE, ownerRepos.length);
  const pageRepos = ownerRepos.slice(pageStart, pageEnd);

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <RequireLogin />;

  const canEdit = session.data.is_admin;
  const autoReviewCount = ownerRepos.filter((r) => autoReviewSet.has(r.full_name)).length;
  const loading = repos.isLoading || autoReview.isLoading;

  return (
    <AppShell
      user={session.data}
      title={owner}
      description={
        canEdit
          ? "Choose which repositories run Open SWE Review automatically. All installed repositories remain available for on-demand reviews."
          : "Automatic review settings are read-only for non-admins."
      }
      backTo={{ to: "/review", label: "Back to Open SWE Review" }}
    >
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Repositories
          </h2>
          <span className="text-xs text-muted-foreground">
            {autoReviewCount}/{ownerRepos.length} run automatically
          </span>
        </div>
        <div className="rounded-lg border border-border bg-card">
          {loading && (
            <div className="p-4">
              <Skeleton className="h-32 w-full" />
            </div>
          )}
          {!loading && ownerRepos.length === 0 && (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              No repositories found for this installation.
            </p>
          )}
          <ul className="divide-y divide-border">
            {pageRepos.map((r) => {
              const runsAutomatically = autoReviewSet.has(r.full_name);
              return (
                <li
                  key={r.full_name}
                  className="flex items-center justify-between gap-4 px-4 py-3"
                >
                  <div className="flex min-w-0 items-center gap-2 text-xs">
                    <span className="truncate">
                      <span className="text-muted-foreground">{owner}/</span>
                      <span className="font-medium text-foreground">
                        {r.full_name.slice(owner.length + 1)}
                      </span>
                    </span>
                    {r.private && (
                      <span className="text-[10px] text-muted-foreground">private</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Run automatically</span>
                    <span
                      title={
                        !canEdit
                          ? "Only team admins can modify automatic review settings"
                          : undefined
                      }
                      className={!canEdit ? "cursor-not-allowed" : undefined}
                    >
                      <Switch
                        aria-label={`Run reviews automatically for ${r.full_name}`}
                        checked={runsAutomatically}
                        disabled={!canEdit || toggleAutoReview.isPending}
                        onCheckedChange={(v) =>
                          toggleAutoReview.mutate({ full_name: r.full_name, on: v })
                        }
                      />
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
          {ownerRepos.length > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-4 border-t border-border px-4 py-2 text-xs">
              <span className="text-muted-foreground">
                Showing {pageStart + 1}-{pageEnd} of {ownerRepos.length}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={safePage === 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  Prev
                </Button>
                <span className="text-muted-foreground">
                  {safePage + 1} / {totalPages}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={safePage >= totalPages - 1}
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>
      </section>
    </AppShell>
  );
}
