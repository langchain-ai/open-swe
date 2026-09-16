"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { ModelOption, TeamSettings } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import { RepoSelector } from "@/features/settings/components/RepoSelector"

type GatewayMode = "inherit" | "enabled" | "disabled"

function gatewayMode(value: boolean | null | undefined): GatewayMode {
  if (value === true) return "enabled"
  if (value === false) return "disabled"
  return "inherit"
}

function gatewayModeValue(mode: GatewayMode): boolean | null {
  if (mode === "enabled") return true
  if (mode === "disabled") return false
  return null
}

interface TeamSettingsSave {
  body: TeamSettings
  workspace: string
}

/**
 * Saves team settings for `workspace` and caches the response under that
 * workspace. The target travels in the mutation variables because TanStack
 * Query rebinds a pending mutation's callbacks to the latest render: a save
 * started before the admin switched workspaces must not land in the newly
 * selected workspace's cache.
 */
export function useSaveTeamSettings(workspace: string, onSaved?: () => void) {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: (variables: TeamSettingsSave) =>
      api.saveTeamSettings(variables.body, variables.workspace),
    onSuccess: (saved, variables) => {
      qc.setQueryData(["teamSettings", variables.workspace], saved)
      setError(null)
      onSaved?.()
    },
    onError: (e: Error) => setError(e.message),
  })
  return {
    mutate: (body: TeamSettings) => mutation.mutate({ body, workspace }),
    isPending: mutation.isPending,
    error,
  }
}

export function LLMGatewaySection({ workspace }: { workspace: string }) {
  const settings = useQuery({
    queryKey: ["teamSettings", workspace],
    queryFn: () => api.getTeamSettings(workspace),
  })
  const save = useSaveTeamSettings(workspace)

  const mode = gatewayMode(settings.data?.gateway_enabled)

  return (
    <SettingsSection
      title="LLM Gateway"
      description="Route agent and reviewer LLM calls through the LangSmith LLM Gateway. It authenticates with the workspace LangSmith API key and resolves provider keys from Provider Secrets, so no provider keys are needed at runtime. Requires the gateway (private beta) enabled for your organization."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="Route through the gateway"
          description="Inherit uses the LANGSMITH_GATEWAY_ENABLED deployment default. OpenAI, Anthropic, Fireworks, and Google Gemini are routed; other providers call the provider directly."
          control={
            <Select
              value={mode}
              onValueChange={(next) =>
                settings.data &&
                save.mutate({
                  ...settings.data,
                  gateway_enabled: gatewayModeValue(next as GatewayMode),
                })
              }
              disabled={!settings.data || save.isPending}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inherit">
                  Inherit deployment default
                </SelectItem>
                <SelectItem value="enabled">Enabled</SelectItem>
                <SelectItem value="disabled">Disabled</SelectItem>
              </SelectContent>
            </Select>
          }
        />
      </div>
      {save.error && (
        <p className="px-4 pb-3 text-xs text-destructive">{save.error}</p>
      )}
    </SettingsSection>
  )
}

export function FableSection({ workspace }: { workspace: string }) {
  const qc = useQueryClient()
  const settings = useQuery({
    queryKey: ["teamSettings", workspace],
    queryFn: () => api.getTeamSettings(workspace),
  })
  // Refresh the pickers so Fable appears or disappears.
  const save = useSaveTeamSettings(workspace, () =>
    qc.invalidateQueries({ queryKey: ["options"] })
  )
  return (
    <SettingsSection
      title="Fable"
      description="Claude Fable 5.1 runs safety classifiers that inspect and may retain requests, so it is not compatible with Zero Data Retention (ZDR). Off by default; enable only if your workspace does not require ZDR."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="Allow Fable models"
          description="When on, Fable 5.1 is selectable for individual agent, reviewer, and chat runs, but cannot be saved as a default. When off, it is hidden and any run that resolves to Fable falls back to Opus."
          control={
            <Switch
              checked={!!settings.data?.fable_enabled}
              onCheckedChange={(next) =>
                settings.data &&
                save.mutate({ ...settings.data, fable_enabled: next })
              }
              disabled={!settings.data || save.isPending}
            />
          }
        />
      </div>
      {save.error && (
        <p className="px-4 pb-3 text-xs text-destructive">{save.error}</p>
      )}
    </SettingsSection>
  )
}

export function WorkspaceDefaultsSection({
  workspace,
  models,
  repositories,
}: {
  workspace: string
  models: Array<ModelOption>
  /** The workspace's own repositories; the default repository is one of them. */
  repositories: Array<string>
}) {
  const settings = useQuery({
    queryKey: ["teamSettings", workspace],
    queryFn: () => api.getTeamSettings(workspace),
  })
  const save = useSaveTeamSettings(workspace)

  return (
    <SettingsSection
      title="Model defaults"
      description="Models for runs in this workspace. Per-user Cloud Agent selections override the agent defaults."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="Adaptive model routing"
          description="Automatically choose a model for each turn, org-wide. Users can still override this in their personal settings."
          control={
            <Switch
              checked={settings.data?.model_routing_enabled ?? false}
              onCheckedChange={(next) =>
                settings.data &&
                save.mutate({ ...settings.data, model_routing_enabled: next })
              }
              disabled={!settings.data || save.isPending}
            />
          }
        />
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
          effort={
            settings.data?.default_agent_subagent_reasoning_effort ?? null
          }
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
        <RolePicker
          label="Agent routing: fast"
          description="Model used for straightforward agent turns."
          models={models}
          model={settings.data?.default_agent_routing_fast_model ?? null}
          effort={
            settings.data?.default_agent_routing_fast_reasoning_effort ?? null
          }
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_agent_routing_fast_model: model,
              default_agent_routing_fast_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
        <RolePicker
          label="Agent routing: balanced"
          description="Model used for ordinary implementation and investigation turns."
          models={models}
          model={settings.data?.default_agent_routing_balanced_model ?? null}
          effort={
            settings.data?.default_agent_routing_balanced_reasoning_effort ??
            null
          }
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_agent_routing_balanced_model: model,
              default_agent_routing_balanced_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
        <RolePicker
          label="Agent routing: performance"
          description="Model used for complex reasoning and plan-mode turns."
          models={models}
          model={settings.data?.default_agent_routing_performance_model ?? null}
          effort={
            settings.data?.default_agent_routing_performance_reasoning_effort ??
            null
          }
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_agent_routing_performance_model: model,
              default_agent_routing_performance_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
        <RolePicker
          label="Thread title generation"
          description="Model used to name new agent threads in the background."
          models={models}
          model={settings.data?.default_thread_title_model ?? null}
          effort={settings.data?.default_thread_title_reasoning_effort ?? null}
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_thread_title_model: model,
              default_thread_title_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
        <SettingsRow
          label="Default Repository"
          description="Used when a run in this workspace has no explicit repository and the user has no profile default."
          control={
            <div className="w-56">
              <RepoSelector
                repos={repositories.map((full_name) => ({ full_name }))}
                selectedRepo={settings.data?.default_repo ?? null}
                onRepoChange={(repo) =>
                  settings.data &&
                  save.mutate({ ...settings.data, default_repo: repo })
                }
                placeholder="Pick a repository…"
                emptySelectionLabel="No default repository"
                triggerClassName="h-7 w-full max-w-none rounded-md border border-input bg-input/20 px-2 py-1.5 text-xs/relaxed text-foreground transition-colors hover:opacity-100 dark:bg-input/30"
                dropdownClassName="w-56"
                disabled={!settings.data || save.isPending}
              />
            </div>
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
          effort={
            settings.data?.default_reviewer_subagent_reasoning_effort ?? null
          }
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
        <RolePicker
          label="Open SWE Review Diff Grouping"
          description="Model used for the review's 'AI sorted' view that groups changed files into a logical walkthrough. Inherits the Reviewer subagent default when unset."
          models={models}
          model={settings.data?.default_grouping_model ?? null}
          effort={settings.data?.default_grouping_reasoning_effort ?? null}
          inheritLabel="Reviewer subagent default"
          onInherit={() =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_grouping_model: null,
              default_grouping_reasoning_effort: null,
            })
          }
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_grouping_model: model,
              default_grouping_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
        <RolePicker
          label="Open SWE Review Chat"
          description="Model used by the 'chat with this PR' assistant on the review page. Inherits the Agent default when unset."
          models={models}
          model={settings.data?.default_chat_model ?? null}
          effort={settings.data?.default_chat_reasoning_effort ?? null}
          inheritLabel="Agent default"
          onInherit={() =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_chat_model: null,
              default_chat_reasoning_effort: null,
            })
          }
          onChange={(model, effort) =>
            settings.data &&
            save.mutate({
              ...settings.data,
              default_chat_model: model,
              default_chat_reasoning_effort: effort,
            })
          }
          disabled={!settings.data || save.isPending}
        />
      </div>
      {save.error && (
        <p className="px-4 pb-3 text-xs text-destructive">{save.error}</p>
      )}
    </SettingsSection>
  )
}

interface RolePickerProps {
  label: string
  description: string
  models: Array<ModelOption>
  model: string | null
  effort: string | null
  onChange: (model: string, effort: string) => void
  disabled: boolean
  /**
   * When set, the model dropdown gains a leading "inherit" option with this
   * label. Selecting it calls {@link onInherit} (clearing the override); an
   * unset `model` renders as this option.
   */
  inheritLabel?: string
  onInherit?: () => void
}

const INHERIT_VALUE = "__inherit__"

function RolePicker({
  label,
  description,
  models,
  model,
  effort,
  onChange,
  disabled,
  inheritLabel,
  onInherit,
}: RolePickerProps) {
  const inheritFallback = inheritLabel ? INHERIT_VALUE : ""
  const [localModel, setLocalModel] = useState<string>(model ?? inheritFallback)
  const [localEffort, setLocalEffort] = useState<string>(effort ?? "")

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setLocalModel(model ?? inheritFallback)
    setLocalEffort(effort ?? "")
  }, [model, effort, inheritFallback])

  const isInherit = localModel === INHERIT_VALUE
  const selectedModel = models.find((m) => m.id === localModel)
  const availableEfforts = selectedModel?.efforts ?? []

  const handleModelChange = (value: string | null) => {
    if (!value) return
    if (value === INHERIT_VALUE) {
      setLocalModel(INHERIT_VALUE)
      setLocalEffort("")
      onInherit?.()
      return
    }
    const nextModel = models.find((m) => m.id === value)
    if (!nextModel) return
    const nextEffort = nextModel.efforts.includes(localEffort)
      ? localEffort
      : nextModel.default_effort
    setLocalModel(value)
    setLocalEffort(nextEffort)
    onChange(value, nextEffort)
  }

  const handleEffortChange = (value: string | null) => {
    if (!value || !localModel || isInherit) return
    setLocalEffort(value)
    onChange(localModel, value)
  }

  return (
    <SettingsRow
      label={label}
      description={description}
      control={
        <div className="flex items-center gap-2">
          <Select
            value={localModel}
            onValueChange={handleModelChange}
            disabled={disabled}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {inheritLabel && (
                <SelectItem value={INHERIT_VALUE}>{inheritLabel}</SelectItem>
              )}
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
            disabled={disabled || !localModel || isInherit}
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
  )
}
