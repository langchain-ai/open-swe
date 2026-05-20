import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { buildProfileUpdate, useOptions, useProfile, useSaveProfile } from "@/lib/profile";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/my-settings")({ component: MySettingsPage });

function MySettingsPage() {
  const session = useSession();
  const profile = useProfile();
  const options = useOptions();
  const save = useSaveProfile();

  const [prDestination, setPrDestination] = useState<string>("team_default");
  const [error, setError] = useState<string | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (!profile.data || initialized.current) return;
    initialized.current = true;
    setPrDestination(profile.data.preferred_pr_destination ?? "team_default");
  }, [profile.data]);

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  const firstModel = options.data?.models[0];

  const handleSavePrDestination = (value: string | null) => {
    if (!value) return;
    setPrDestination(value);
    const body = buildProfileUpdate(
      profile.data,
      { preferred_pr_destination: value === "team_default" ? null : value },
      firstModel?.id ?? "",
      firstModel?.default_effort ?? "",
    );
    save.mutateAsync(body).catch((e: Error) => setError(e.message));
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

      <SettingsSection title="PR Preferences">
        <SettingsRow
          label="Preferred PR destination"
          description="Choose where PR links open across web, the desktop app and IDE."
          control={
            <Select value={prDestination} onValueChange={handleSavePrDestination}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="team_default">Team default</SelectItem>
                <SelectItem value="web">Web</SelectItem>
                <SelectItem value="desktop">Desktop App</SelectItem>
                <SelectItem value="ide">IDE</SelectItem>
              </SelectContent>
            </Select>
          }
        />
      </SettingsSection>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  );
}
