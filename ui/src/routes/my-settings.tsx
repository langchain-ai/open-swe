import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [prDestination, setPrDestination] = useState<string>("team_default");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!profile.data) return;
    setFirstName(profile.data.first_name ?? "");
    setLastName(profile.data.last_name ?? "");
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
  const handleSaveProfile = () => {
    setError(null);
    const body = buildProfileUpdate(
      profile.data,
      { first_name: firstName || null, last_name: lastName || null },
      firstModel?.id ?? "",
      firstModel?.default_effort ?? "",
    );
    save
      .mutateAsync(body)
      .catch((e: Error) => setError(e.message));
  };

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
        <div className="divide-y divide-border">
          <SettingsRow
            label="Email"
            control={
              <span className="text-xs text-muted-foreground">
                {session.data.email ?? "—"}
              </span>
            }
          />
          <SettingsRow
            label="First Name"
            htmlFor="first-name"
            control={
              <Input
                id="first-name"
                className="w-56"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
              />
            }
          />
          <SettingsRow
            label="Last Name"
            htmlFor="last-name"
            control={
              <Input
                id="last-name"
                className="w-56"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
              />
            }
          />
          <div className="flex justify-end px-4 py-3">
            <Button size="sm" onClick={handleSaveProfile} disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
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
