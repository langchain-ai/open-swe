import { Navigate, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import type { SessionUser } from "@/lib/api";
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
import { api } from "@/lib/api";
import { buildProfileUpdate, useOptions, useProfile, useSaveProfile } from "@/lib/profile";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

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

function UserMappingSection({ session }: { session: SessionUser }) {
  const qc = useQueryClient();
  const mapping = useQuery({ queryKey: ["myMapping"], queryFn: api.myMapping });
  const [workEmail, setWorkEmail] = useState("");
  const [slackId, setSlackId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (mapping.isLoading || initialized.current) return;
    initialized.current = true;
    setWorkEmail(mapping.data?.work_email ?? session.email ?? "");
    setSlackId(mapping.data?.slack_user_id ?? "");
  }, [mapping.isLoading, mapping.data, session.email]);

  const save = useMutation({
    mutationFn: () =>
      api.saveMyMapping({
        work_email: workEmail.trim(),
        slack_user_id: slackId.trim() || null,
      }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["myMapping"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const linked = !!mapping.data?.work_email;

  return (
    <SettingsSection
      title="User mapping"
      description="Link your GitHub account to your work email so Open SWE can act as you when you tag it from Slack or Linear. Your GitHub email may differ from your work email — set the one your Slack/Linear account uses."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="GitHub account"
          control={
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">{session.login}</span>
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10px] font-medium",
                  linked ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground",
                )}
              >
                {linked ? "Linked" : "Not linked"}
              </span>
            </div>
          }
        />
        <SettingsRow
          label="Work email"
          description="The email address tied to your Slack and Linear accounts."
          htmlFor="work-email"
          control={
            <Input
              id="work-email"
              className="w-64"
              placeholder="you@company.com"
              type="email"
              value={workEmail}
              onChange={(e) => setWorkEmail(e.target.value)}
            />
          }
        />
        <SettingsRow
          label="Slack member ID"
          description="Optional. Found in Slack under your profile → ⋯ → Copy member ID (starts with U)."
          htmlFor="slack-id"
          control={
            <Input
              id="slack-id"
              className="w-64"
              placeholder="U01234567 (optional)"
              value={slackId}
              onChange={(e) => setSlackId(e.target.value)}
            />
          }
        />
        <div className="flex justify-end px-4 py-3">
          <Button
            size="sm"
            onClick={() => save.mutate()}
            disabled={!workEmail.trim() || save.isPending || mapping.isLoading}
          >
            {save.isPending ? "Saving…" : "Save mapping"}
          </Button>
        </div>
      </div>
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  );
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
    <AppShell user={session.data} title="Profile Settings">
      <SettingsSection title="Profile">
        <SettingsRow
          label="Email"
          control={
            <span className="text-xs text-muted-foreground">{session.data.email ?? "—"}</span>
          }
        />
      </SettingsSection>

      <UserMappingSection session={session.data} />

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
