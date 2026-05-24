import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { ModelOption, Profile, ProfileUpdate, TeamSettings } from "@/lib/api";
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell";
import { ProfileForm } from "@/components/ProfileForm";
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
      description="Workspace-wide defaults and per-user profile edits."
    >
      <GlobalDefaultsSection models={options.data?.models ?? []} />

      <SettingsSection title="Per-user profiles">
        <div className="grid grid-cols-1 gap-0 md:grid-cols-[260px_1fr]">
          <div className="flex flex-col gap-0.5 border-b border-border p-2 md:border-b-0 md:border-r">
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
          <div className="p-4">
            {!activeProfile ? (
              <p className="text-xs text-muted-foreground">
                Pick a user on the left to edit their profile.
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
        </div>
      </SettingsSection>
    </AppShell>
  );
}

function GlobalDefaultsSection({ models }: { models: Array<ModelOption> }) {
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
  });
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: TeamSettings) => api.saveTeamSettings(body),
    onSuccess: (saved) => {
      qc.setQueryData(["teamSettings"], saved);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <SettingsSection
      title="Global defaults"
      description="Workspace-wide model defaults. Per-user Cloud Agent selections override these for the agent."
    >
      <div className="divide-y divide-border">
        <RolePicker
          label="Open SWE Agent"
          description="Model used for code-writing runs triggered from Slack, Linear, GitHub, and Cloud Agents."
          models={models}
          model={settings.data?.default_agent_model ?? null}
          effort={settings.data?.default_agent_reasoning_effort ?? null}
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_agent_model: model,
              default_agent_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
        <RolePicker
          label="Open SWE Reviewer"
          description="Model used for PR review runs."
          models={models}
          model={settings.data?.default_reviewer_model ?? null}
          effort={settings.data?.default_reviewer_reasoning_effort ?? null}
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_reviewer_model: model,
              default_reviewer_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
      </div>
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  );
}

interface RolePickerProps {
  label: string;
  description: string;
  models: Array<ModelOption>;
  model: string | null;
  effort: string | null;
  onChange: (model: string, effort: string) => void;
  disabled: boolean;
}

function RolePicker({
  label,
  description,
  models,
  model,
  effort,
  onChange,
  disabled,
}: RolePickerProps) {
  const [localModel, setLocalModel] = useState<string>(model ?? "");
  const [localEffort, setLocalEffort] = useState<string>(effort ?? "");

  useEffect(() => {
    setLocalModel(model ?? "");
    setLocalEffort(effort ?? "");
  }, [model, effort]);

  const selectedModel = models.find((m) => m.id === localModel);
  const availableEfforts = selectedModel?.efforts ?? [];

  const handleModelChange = (value: string | null) => {
    if (!value) return;
    const nextModel = models.find((m) => m.id === value);
    if (!nextModel) return;
    const nextEffort = nextModel.efforts.includes(localEffort)
      ? localEffort
      : nextModel.default_effort;
    setLocalModel(value);
    setLocalEffort(nextEffort);
    onChange(value, nextEffort);
  };

  const handleEffortChange = (value: string | null) => {
    if (!value || !localModel) return;
    setLocalEffort(value);
    onChange(localModel, value);
  };

  return (
    <SettingsRow
      label={label}
      description={description}
      control={
        <div className="flex items-center gap-2">
          <Select value={localModel} onValueChange={handleModelChange} disabled={disabled}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {models.map((m) => (
                <SelectItem key={m.id} value={m.id}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={localEffort}
            onValueChange={handleEffortChange}
            disabled={disabled || !localModel}
          >
            <SelectTrigger className="w-28">
              <SelectValue placeholder="effort" />
            </SelectTrigger>
            <SelectContent>
              {availableEfforts.map((e) => (
                <SelectItem key={e} value={e}>
                  {e}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      }
    />
  );
}
