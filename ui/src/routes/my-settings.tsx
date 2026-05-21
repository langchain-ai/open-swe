import { Navigate, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";

import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/my-settings")({ component: MySettingsPage });

function MySettingsPage() {
  const session = useSession();
  const qc = useQueryClient();
  const navigate = useNavigate();

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  const handleLogout = async () => {
    await api.logout();
    qc.setQueryData(["session"], null);
    void navigate({ to: "/login" });
  };

  return (
    <AppShell user={session.data} title="My Settings">
      <SettingsSection title="Profile">
        <SettingsRow
          label="Email"
          control={
            <span className="text-xs text-muted-foreground">
              {session.data.email ?? "—"}
            </span>
          }
        />
      </SettingsSection>

      <SettingsSection title="Account">
        <SettingsRow
          label="Sign out"
          description="End your dashboard session."
          control={
            <Button size="sm" variant="outline" onClick={() => void handleLogout()}>
              Sign out
            </Button>
          }
        />
      </SettingsSection>
    </AppShell>
  );
}
