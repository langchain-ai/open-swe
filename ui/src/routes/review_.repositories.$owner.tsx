import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import type { ReposPayload } from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";

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

  const enabled = useQuery({
    queryKey: ["enabledReviewRepos"],
    queryFn: api.listEnabledReviewRepos,
    enabled: !!session.data,
  });

  const toggle = useMutation({
    mutationFn: ({ full_name, on }: { full_name: string; on: boolean }) =>
      api.setEnabledReviewRepo(full_name, on),
    onSuccess: (data) => {
      qc.setQueryData(["enabledReviewRepos"], data);
    },
  });

  const ownerRepos = useMemo(
    () =>
      (repos.data?.repositories ?? [])
        .filter((r) => r.full_name.split("/")[0] === owner)
        .sort((a, b) => a.full_name.localeCompare(b.full_name)),
    [repos.data?.repositories, owner],
  );

  const enabledSet = useMemo(
    () => new Set(enabled.data?.repos ?? []),
    [enabled.data?.repos],
  );

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  const canEdit = session.data.is_admin;
  const enabledCount = ownerRepos.filter((r) => enabledSet.has(r.full_name)).length;
  const loading = repos.isLoading || enabled.isLoading;

  return (
    <AppShell
      user={session.data}
      title={owner}
      description={
        canEdit
          ? "Toggle a repository to opt it into automatic Open SWE Review."
          : "Only team admins can modify enabled repositories."
      }
      backTo={{ to: "/review", label: "Back to Open SWE Review" }}
    >
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Repositories
          </h2>
          <span className="text-xs text-muted-foreground">
            {enabledCount}/{ownerRepos.length} enabled
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
            {ownerRepos.map((r) => {
              const isEnabled = enabledSet.has(r.full_name);
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
                  <Switch
                    checked={isEnabled}
                    disabled={!canEdit || toggle.isPending}
                    onCheckedChange={(v) =>
                      toggle.mutate({ full_name: r.full_name, on: v })
                    }
                  />
                </li>
              );
            })}
          </ul>
        </div>
      </section>
    </AppShell>
  );
}
