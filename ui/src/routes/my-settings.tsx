import { Navigate, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { buildProfileUpdate, useOptions, useProfile, useSaveProfile } from "@/lib/profile";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/my-settings")({ component: MySettingsPage });

type DraftReviewChoice = "team_default" | "always_on" | "always_off";

function toChoice(value: boolean | null | undefined): DraftReviewChoice {
  if (value === true) return "always_on";
  if (value === false) return "always_off";
  return "team_default";
}

function fromChoice(choice: DraftReviewChoice): boolean | null {
  if (choice === "always_on") return true;
  if (choice === "always_off") return false;
  return null;
}

function MySettingsPage() {
  const session = useSession();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const profile = useProfile();
  const options = useOptions();
  const save = useSaveProfile();
  const teamSettings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
    enabled: !!session.data,
  });
  const [error, setError] = useState<string | null>(null);

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

  const firstModel = options.data?.models[0];
  const fallbackModel = firstModel?.id ?? "";
  const fallbackEffort = firstModel?.default_effort ?? "";

  const draftChoice = toChoice(profile.data?.review_draft_prs);
  const teamDefaultOn = teamSettings.data?.review_draft_prs ?? false;
  const teamDefaultLabel = `Use team default (currently: ${teamDefaultOn ? "On" : "Off"})`;

  const handleDraftChoiceChange = (next: DraftReviewChoice) => {
    setError(null);
    save
      .mutateAsync(
        buildProfileUpdate(
          profile.data,
          { review_draft_prs: fromChoice(next) },
          fallbackModel,
          fallbackEffort,
        ),
      )
      .catch((e: Error) => setError(e.message));
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

      <SettingsSection title="Open SWE Review">
        <SettingsRow
          label="Review my draft PRs"
          description="Whether Open SWE Review runs on pull requests you open in draft. When set to the team default, your admin's org-wide setting applies."
          control={
            <Select
              value={draftChoice}
              onValueChange={(v) => handleDraftChoiceChange(v as DraftReviewChoice)}
              disabled={profile.isLoading || save.isPending}
            >
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="team_default">{teamDefaultLabel}</SelectItem>
                <SelectItem value="always_on">Always review my drafts</SelectItem>
                <SelectItem value="always_off">Never review my drafts</SelectItem>
              </SelectContent>
            </Select>
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

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  );
}
