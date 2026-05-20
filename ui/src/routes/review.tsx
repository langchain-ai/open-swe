import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { AutofixMode, TeamSettings, TriggerMode } from "@/lib/api";
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell";
import { ReviewStylesPanel } from "@/components/ReviewStylesPanel";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/review")({ component: ReviewPage });

const TRIGGER_MODES: Array<{ value: TriggerMode; label: string; description: string }> = [
  { value: "every_push", label: "Every Push", description: "Review every push to a PR" },
  {
    value: "ready_for_review",
    label: "Ready For Review",
    description: "Only when the PR is marked ready for review",
  },
  { value: "manual", label: "Manual", description: "Review only when explicitly invoked" },
];

const AUTOFIX_MODES: Array<{ value: AutofixMode; label: string }> = [
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

const DEFAULT_SETTINGS: TeamSettings = {
  trigger_mode: "every_push",
  review_draft_prs: false,
  pr_summaries: true,
  autofix_mode: "off",
  autofix_severity_threshold: "medium",
};

function ReviewPage() {
  const session = useSession();
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
    enabled: !!session.data,
  });
  const [local, setLocal] = useState<TeamSettings>(DEFAULT_SETTINGS);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (settings.data) setLocal(settings.data);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (body: TeamSettings) => api.saveTeamSettings(body),
    onSuccess: (saved) => {
      qc.setQueryData(["teamSettings"], saved);
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

  const current: TeamSettings = local;
  const canEdit = session.data.is_admin;

  const persist = (patch: Partial<TeamSettings>) => {
    const next: TeamSettings = { ...current, ...patch };
    setLocal(next);
    if (canEdit) save.mutate(next);
  };

  const triggerDescription =
    TRIGGER_MODES.find((m) => m.value === current.trigger_mode)?.description ??
    "Open SWE Review will automatically review every push to a PR";

  return (
    <AppShell
      user={session.data}
      title="Open SWE Review"
      description="Automatically review pull requests for bugs and issues. Runs are billed based on underlying agent usage."
    >
      <SettingsSection title="Configuration">
        <div className="divide-y divide-border">
          <SettingsRow
            label="Trigger Mode"
            description={triggerDescription}
            control={
              <Select
                value={current.trigger_mode}
                onValueChange={(v) => persist({ trigger_mode: v as TriggerMode })}
                disabled={!canEdit}
              >
                <SelectTrigger className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TRIGGER_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="Review Draft PRs"
            description="Allow Open SWE Review to automatically review draft pull requests"
            control={
              <Switch
                checked={current.review_draft_prs}
                onCheckedChange={(v) => persist({ review_draft_prs: v })}
                disabled={!canEdit}
              />
            }
          />
          <SettingsRow
            label="PR Summaries"
            description="Generate descriptions on pull requests"
            control={
              <Switch
                checked={current.pr_summaries}
                onCheckedChange={(v) => persist({ pr_summaries: v })}
                disabled={!canEdit}
              />
            }
          />
          <SettingsRow
            label="Autofix Mode"
            description="When enabled, the reviewer will propose fixes. Billed at plan rates."
            control={
              <Select
                value={current.autofix_mode}
                onValueChange={(v) => persist({ autofix_mode: v as AutofixMode })}
                disabled={!canEdit}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AUTOFIX_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
          <SettingsRow
            label="Autofix Severity Threshold"
            description="Findings at this severity or higher are auto-fixed"
            control={
              <Select
                value={current.autofix_severity_threshold}
                onValueChange={(v) =>
                  persist({ autofix_severity_threshold: v as AutofixMode })
                }
                disabled={!canEdit}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AUTOFIX_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
        </div>
      </SettingsSection>

      {!canEdit && (
        <p className="text-xs text-muted-foreground">
          These settings are read-only. Ask a workspace admin to change them.
        </p>
      )}

      <SettingsSection
        title="Review Style Prompts"
        description="An agent browses recent merged PR review feedback on GitHub, then writes a per-repo style guide for the reviewer."
      >
        <ReviewStylesPanel />
      </SettingsSection>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  );
}
