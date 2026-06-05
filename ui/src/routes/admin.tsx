import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { ModelOption, TeamSettings, UserMapping } from "@/lib/api";
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
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/admin")({ component: AdminPage });

function AdminPage() {
  const session = useSession();

  const options = useQuery({
    queryKey: ["options"],
    queryFn: api.options,
    enabled: !!session.data?.is_admin,
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

  return (
    <AppShell
      user={session.data}
      title="Admin"
      description="Workspace-wide defaults and user mappings."
    >
      <GlobalDefaultsSection models={options.data?.models ?? []} />

      <UserMappingsSection enabled={!!session.data.is_admin} />
    </AppShell>
  );
}

const PAGE_SIZE = 20;

function UserMappingsSection({ enabled }: { enabled: boolean }) {
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const mappings = useQuery({
    queryKey: ["adminUserMappings", page],
    queryFn: () => api.adminListUserMappings(page, PAGE_SIZE),
    enabled,
  });

  const total = mappings.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  useEffect(() => {
    if (!mappings.isFetching && page > pageCount) {
      setPage(pageCount);
    }
  }, [mappings.isFetching, page, pageCount]);

  const remove = useMutation({
    mutationFn: (gh: string) => api.adminDeleteUserMapping(gh),
    onSuccess: () => void mappings.refetch(),
    onError: (e: Error) => setError(e.message),
  });

  const items = mappings.data?.items ?? [];

  return (
    <SettingsSection
      title="User mappings"
      description="Mappings are created when users connect Slack from settings. Admins can remove stale mappings here."
    >
      <div className="flex flex-col gap-3 p-4">
        {error && <span className="text-xs text-destructive">{error}</span>}

        <div className="flex flex-col gap-0.5">
          {mappings.isLoading ? (
            <Skeleton className="h-32" />
          ) : !items.length ? (
            <p className="text-xs text-muted-foreground">No mappings yet.</p>
          ) : (
            items.map((m: UserMapping) => (
              <div
                key={m.github_login}
                className="flex items-center justify-between gap-2 border-b border-border py-1.5 text-sm last:border-b-0"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="truncate font-medium">{m.github_login}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {m.work_email}
                    {m.slack_user_id ? ` · ${m.slack_user_id}` : ""}
                    {m.source ? ` · ${m.source}` : ""}
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove.mutate(m.github_login)}
                  disabled={remove.isPending}
                >
                  Remove
                </Button>
              </div>
            ))
          )}
        </div>

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
            <span>
              {total} mapping{total === 1 ? "" : "s"} · page {page} of {pageCount}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || mappings.isFetching}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                disabled={page >= pageCount || mappings.isFetching}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}

function GlobalDefaultsSection({ models }: { models: Array<ModelOption> }) {
  const qc = useQueryClient();
  const settings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
  });
  const [error, setError] = useState<string | null>(null);
  const [defaultRepoDraft, setDefaultRepoDraft] = useState("");

  useEffect(() => {
    setDefaultRepoDraft(settings.data?.default_repo ?? "");
  }, [settings.data?.default_repo]);

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
      description="Workspace-wide model defaults. Per-user Cloud Agent selections override the agent defaults."
    >
      <div className="divide-y divide-border">
        <RolePicker
          label="Open SWE Agent"
          description="Model used for code-writing runs triggered from Slack, Linear, GitHub, and the Open SWE Agent."
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
          label="Open SWE Agent subagents"
          description="Model used by delegated main-agent tasks."
          models={models}
          model={settings.data?.default_agent_subagent_model ?? null}
          effort={settings.data?.default_agent_subagent_reasoning_effort ?? null}
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_agent_subagent_model: model,
              default_agent_subagent_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
        <SettingsRow
          label="Default Repository"
          description="Global fallback used when a run has no explicit repo and the user has no profile default. Use owner/repo."
          control={
            <Input
              className="w-56"
              placeholder="owner/repo"
              value={defaultRepoDraft}
              onChange={(e) => setDefaultRepoDraft(e.target.value)}
              onBlur={() =>
                settings.data &&
                save.mutate({
                  ...settings.data,
                  default_repo: defaultRepoDraft.trim() || null,
                })
              }
              disabled={!settings.data || save.isPending}
            />
          }
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
        <RolePicker
          label="Open SWE Reviewer subagents"
          description="Model used by delegated reviewer tasks."
          models={models}
          model={settings.data?.default_reviewer_subagent_model ?? null}
          effort={settings.data?.default_reviewer_subagent_reasoning_effort ?? null}
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_reviewer_subagent_model: model,
              default_reviewer_subagent_reasoning_effort: effort,
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
