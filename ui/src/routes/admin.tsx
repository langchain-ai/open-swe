import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { Profile, ProfileUpdate } from "@/lib/api";
import { AppShell, SettingsSection } from "@/components/AppShell";
import { ProfileForm } from "@/components/ProfileForm";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/admin")({ component: AdminPage });

function AdminPage() {
  const session = useSession();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const options = useQuery({
    queryKey: ["options"],
    queryFn: api.options,
    enabled: !!session.data?.is_admin,
  });

  const profiles = useQuery({
    queryKey: ["adminProfiles"],
    queryFn: api.adminListProfiles,
    enabled: !!session.data?.is_admin,
  });

  const save = useMutation({
    mutationFn: ({ login, body }: { login: string; body: ProfileUpdate }) =>
      api.adminSaveProfile(login, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["adminProfiles"] });
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;
  if (!session.data.is_admin) return <Navigate to="/my-settings" />;

  const activeProfile: Profile | null =
    (selected && profiles.data?.find((p) => p.login === selected)) || null;

  return (
    <AppShell
      user={session.data}
      title="Admin"
      description="Edit any user's profile defaults."
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[260px_1fr]">
        <SettingsSection title={`Users · ${profiles.data?.length ?? 0}`}>
          <div className="flex flex-col gap-0.5 p-2">
            {profiles.isLoading ? (
              <Skeleton className="h-32" />
            ) : (
              profiles.data?.map((p) => (
                <Button
                  key={p.login}
                  variant={selected === p.login ? "secondary" : "ghost"}
                  className="justify-start"
                  onClick={() => setSelected(p.login ?? null)}
                >
                  <span className="truncate">{p.login}</span>
                </Button>
              ))
            )}
          </div>
        </SettingsSection>

        <SettingsSection
          title={activeProfile?.login ?? "Select a user"}
          description={activeProfile?.email ?? undefined}
        >
          <div className="p-4">
            {!activeProfile ? (
              <p className="text-xs text-muted-foreground">
                Pick a user on the left to edit.
              </p>
            ) : options.isLoading ? (
              <Skeleton className="h-48" />
            ) : (
              <ProfileForm
                models={options.data?.models ?? []}
                repos={[]}
                initial={activeProfile}
                onSubmit={(body) =>
                  save.mutateAsync({ login: activeProfile.login!, body })
                }
                saving={save.isPending}
                error={error}
              />
            )}
          </div>
        </SettingsSection>
      </div>
    </AppShell>
  );
}
