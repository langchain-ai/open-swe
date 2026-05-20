import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents")({ component: AgentsPage });

function AgentsPage() {
  const session = useSession();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const agents = useQuery({
    queryKey: ["activeAgents"],
    queryFn: api.activeAgents,
    enabled: !!session.data?.is_admin,
    refetchInterval: 10000,
  });
  const config = useQuery({
    queryKey: ["babysitterConfig"],
    queryFn: api.babysitterConfig,
    enabled: !!session.data?.is_admin,
  });
  const saveConfig = useMutation({
    mutationFn: api.saveBabysitterConfig,
    onSuccess: (saved) => {
      qc.setQueryData(["babysitterConfig"], saved);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });
  const cancel = useMutation({
    mutationFn: ({ threadId, runId }: { threadId: string; runId: string }) =>
      api.cancelRun(threadId, runId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["activeAgents"] }),
    onError: (e: Error) => setError(e.message),
  });

  if (session.isLoading) {
    return (
      <main className="container mx-auto p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;
  if (!session.data.is_admin) return <Navigate to="/profile" />;

  const current = config.data ?? {
    enabled: false,
    poll_interval_seconds: 600,
    max_attempts_per_sha: 2,
  };

  return (
    <div className="min-h-svh">
      <AppHeader user={session.data} />
      <main className="container mx-auto grid grid-cols-1 gap-6 p-6 lg:grid-cols-[320px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Babysitter</CardTitle>
            <CardDescription>Configuration for PR babysitting.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {config.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : (
              <>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={current.enabled}
                    onChange={(e) =>
                      saveConfig.mutate({ ...current, enabled: e.currentTarget.checked })
                    }
                  />
                  Enabled
                </label>
                <div className="space-y-2">
                  <Label htmlFor="poll">Poll interval</Label>
                  <Input
                    id="poll"
                    type="number"
                    min={60}
                    value={current.poll_interval_seconds}
                    onChange={(e) =>
                      saveConfig.mutate({
                        ...current,
                        poll_interval_seconds: Number(e.currentTarget.value),
                      })
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="attempts">Max attempts per SHA</Label>
                  <Input
                    id="attempts"
                    type="number"
                    min={0}
                    value={current.max_attempts_per_sha}
                    onChange={(e) =>
                      saveConfig.mutate({
                        ...current,
                        max_attempts_per_sha: Number(e.currentTarget.value),
                      })
                    }
                  />
                </div>
              </>
            )}
            {error && <p className="text-destructive text-sm">{error}</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Active agents</CardTitle>
            <CardDescription>{agents.data?.length ?? 0} running or pending runs</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {agents.isLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : agents.data?.length ? (
              agents.data.map((agent) => {
                const pr = agent.metadata.pr as
                  | { owner?: string; name?: string; number?: number; title?: string }
                  | undefined;
                return (
                  <div
                    key={`${agent.thread_id}:${agent.run_id}`}
                    className="flex items-center gap-4 rounded-md border p-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {agent.assistant_id ?? "agent"} · {agent.status}
                      </div>
                      <div className="text-muted-foreground truncate text-sm">
                        {pr?.owner && pr?.name && pr?.number
                          ? `${pr.owner}/${pr.name}#${pr.number}`
                          : agent.thread_id}
                      </div>
                    </div>
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={cancel.isPending}
                      onClick={() =>
                        cancel.mutate({ threadId: agent.thread_id, runId: agent.run_id })
                      }
                    >
                      Cancel
                    </Button>
                  </div>
                );
              })
            ) : (
              <p className="text-muted-foreground text-sm">No active agents.</p>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
