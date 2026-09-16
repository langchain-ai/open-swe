"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState, type ReactNode } from "react"
import type { ModelOption, WorkspaceSettings } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import {
  useScopedSettings,
  type ScopedSettings,
  type SettingsScope,
  type StringSettingField,
} from "@/features/settings/lib/settingsScope"

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

/**
 * A settings row whose value may come from the tier above. On a workspace it
 * says whether the value is inherited or overridden and offers a way back.
 */
export function TierRow({
  settings,
  fields,
  label,
  description,
  control,
}: {
  settings: ScopedSettings
  fields: Array<keyof WorkspaceSettings>
  label: string
  description: string
  control: ReactNode
}) {
  const scoped = settings.scope.kind === "workspace"
  const inherits = settings.inherits(...fields)
  return (
    <SettingsRow
      label={label}
      description={description}
      badge={scoped ? (inherits ? "Inherited" : "Overridden") : undefined}
      control={
        <div className="flex items-center gap-2">
          {control}
          {scoped && !inherits && (
            <Button
              size="sm"
              variant="ghost"
              disabled={settings.saving}
              onClick={() => settings.reset(...fields)}
              aria-label={`Reset ${label} to the instance value`}
            >
              Reset
            </Button>
          )}
        </div>
      }
    />
  )
}

function SaveError({ settings }: { settings: ScopedSettings }) {
  if (!settings.error) return null
  return <p className="px-4 pb-3 text-xs text-destructive">{settings.error}</p>
}

export function LLMGatewaySection({ scope }: { scope: SettingsScope }) {
  const settings = useScopedSettings(scope)
  const mode = gatewayMode(settings.data?.gateway_enabled)
  const scoped = scope.kind === "workspace"

  return (
    <SettingsSection
      title="LLM Gateway"
      description="Route agent and reviewer LLM calls through the LangSmith LLM Gateway. It authenticates with the workspace LangSmith API key and resolves provider keys from Provider Secrets, so no provider keys are needed at runtime. Requires the gateway (private beta) enabled for your organization."
    >
      <div className="divide-y divide-border">
        <TierRow
          settings={settings}
          fields={["gateway_enabled"]}
          label="Route through the gateway"
          description={
            scoped
              ? "Inherit follows the instance setting. OpenAI, Anthropic, Fireworks, and Google Gemini are routed; other providers call the provider directly."
              : "Inherit uses the LANGSMITH_GATEWAY_ENABLED deployment default. OpenAI, Anthropic, Fireworks, and Google Gemini are routed; other providers call the provider directly."
          }
          control={
            <Select
              value={mode}
              onValueChange={(next) => {
                const value = gatewayModeValue(next as GatewayMode)
                if (value === null && scoped) settings.reset("gateway_enabled")
                else settings.save({ gateway_enabled: value })
              }}
              disabled={!settings.data || settings.saving}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inherit">
                  {scoped
                    ? "Inherit instance setting"
                    : "Inherit deployment default"}
                </SelectItem>
                <SelectItem value="enabled">Enabled</SelectItem>
                <SelectItem value="disabled">Disabled</SelectItem>
              </SelectContent>
            </Select>
          }
        />
      </div>
      <SaveError settings={settings} />
    </SettingsSection>
  )
}

export function FableSection({ scope }: { scope: SettingsScope }) {
  const qc = useQueryClient()
  // Refresh the pickers so Fable appears or disappears.
  const settings = useScopedSettings(scope, () =>
    qc.invalidateQueries({ queryKey: ["options"] })
  )
  return (
    <SettingsSection
      title="Fable"
      description="Claude Fable 5.1 runs safety classifiers that inspect and may retain requests, so it is not compatible with Zero Data Retention (ZDR). Off by default; enable only where ZDR is not required."
    >
      <div className="divide-y divide-border">
        <TierRow
          settings={settings}
          fields={["fable_enabled"]}
          label="Allow Fable models"
          description="When on, Fable 5.1 is selectable for individual agent, reviewer, and chat runs, but cannot be saved as a default. When off, it is hidden and any run that resolves to Fable falls back to Opus."
          control={
            <Switch
              checked={!!settings.data?.fable_enabled}
              onCheckedChange={(next) => settings.save({ fable_enabled: next })}
              disabled={!settings.data || settings.saving}
            />
          }
        />
      </div>
      <SaveError settings={settings} />
    </SettingsSection>
  )
}

export function DefaultRepoSection({
  scope,
  repositories,
}: {
  scope: SettingsScope
  /** On a workspace, its own repositories; on the instance, every repository the installation can see. */
  repositories: Array<string>
}) {
  const settings = useScopedSettings(scope)
  const scoped = scope.kind === "workspace"
  return (
    <SettingsSection
      title="Default repository"
      description={
        scoped
          ? "Where a run in this workspace lands when nothing names a repository. An inherited instance default only applies if this workspace owns it."
          : "Where a run lands when nothing names a repository and the workspace sets no default of its own."
      }
    >
      <div className="divide-y divide-border">
        <TierRow
          settings={settings}
          fields={["default_repo"]}
          label="Repository"
          description="Used when a run names no repository and the user has no profile default."
          control={
            <div className="w-56">
              <RepoSelector
                repos={repositories.map((full_name) => ({ full_name }))}
                selectedRepo={settings.data?.default_repo ?? null}
                onRepoChange={(repo) => settings.save({ default_repo: repo })}
                placeholder="Pick a repository…"
                emptySelectionLabel="No default repository"
                triggerClassName="h-7 w-full max-w-none rounded-md border border-input bg-input/20 px-2 py-1.5 text-xs/relaxed text-foreground transition-colors hover:opacity-100 dark:bg-input/30"
                dropdownClassName="w-56"
                disabled={!settings.data || settings.saving}
              />
            </div>
          }
        />
      </div>
      <SaveError settings={settings} />
    </SettingsSection>
  )
}

interface ModelRowProps {
  settings: ScopedSettings
  models: Array<ModelOption>
  label: string
  description: string
  modelField: StringSettingField
  effortField: StringSettingField
  /** Label of the picker's "unset" option, for roles that fall back to another role. */
  inheritLabel?: string
}

function ModelRow({
  settings,
  models,
  label,
  description,
  modelField,
  effortField,
  inheritLabel,
}: ModelRowProps) {
  const patch = (model: string | null, effort: string | null) => {
    const next: Partial<WorkspaceSettings> = {}
    next[modelField] = model
    next[effortField] = effort
    return next
  }
  return (
    <TierRow
      settings={settings}
      fields={[modelField, effortField]}
      label={label}
      description={description}
      control={
        <ModelPairControl
          models={models}
          model={settings.data?.[modelField] ?? null}
          effort={settings.data?.[effortField] ?? null}
          onChange={(model, effort) => settings.save(patch(model, effort))}
          disabled={!settings.data || settings.saving}
          inheritLabel={inheritLabel}
          onInherit={
            inheritLabel
              ? () =>
                  settings.scope.kind === "instance"
                    ? settings.save(patch(null, null))
                    : settings.reset(modelField, effortField)
              : undefined
          }
        />
      }
    />
  )
}

export function ModelDefaultsSection({
  scope,
  models,
}: {
  scope: SettingsScope
  models: Array<ModelOption>
}) {
  const settings = useScopedSettings(scope)
  const scoped = scope.kind === "workspace"

  return (
    <SettingsSection
      title="Model defaults"
      description={
        scoped
          ? "Models for runs in this workspace. Rows marked Inherited follow the instance defaults; change one to override it here. Per-user Cloud Agent selections override the agent defaults."
          : "Models for runs in every workspace that does not override them. Per-user Cloud Agent selections override the agent defaults."
      }
    >
      <div className="divide-y divide-border">
        <TierRow
          settings={settings}
          fields={["model_routing_enabled"]}
          label="Adaptive model routing"
          description="Automatically choose a model for each turn. Users can still override this in their personal settings."
          control={
            <Switch
              checked={settings.data?.model_routing_enabled ?? false}
              onCheckedChange={(next) =>
                settings.save({ model_routing_enabled: next })
              }
              disabled={!settings.data || settings.saving}
            />
          }
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Open SWE Agent"
          description="Model used for code-writing runs triggered from Slack, Linear, GitHub, and the Open SWE Agent."
          modelField="default_agent_model"
          effortField="default_agent_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Open SWE Agent subagents"
          description="Model used by delegated main-agent tasks."
          modelField="default_agent_subagent_model"
          effortField="default_agent_subagent_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Agent routing: fast"
          description="Model used for straightforward agent turns."
          modelField="default_agent_routing_fast_model"
          effortField="default_agent_routing_fast_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Agent routing: balanced"
          description="Model used for ordinary implementation and investigation turns."
          modelField="default_agent_routing_balanced_model"
          effortField="default_agent_routing_balanced_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Agent routing: performance"
          description="Model used for complex reasoning and plan-mode turns."
          modelField="default_agent_routing_performance_model"
          effortField="default_agent_routing_performance_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Thread title generation"
          description="Model used to name new agent threads in the background."
          modelField="default_thread_title_model"
          effortField="default_thread_title_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Open SWE Reviewer"
          description="Model used for PR review runs."
          modelField="default_reviewer_model"
          effortField="default_reviewer_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Open SWE Reviewer subagents"
          description="Model used by delegated reviewer tasks."
          modelField="default_reviewer_subagent_model"
          effortField="default_reviewer_subagent_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Open SWE Review Diff Grouping"
          description="Model used for the review's 'AI sorted' view that groups changed files into a logical walkthrough. Falls back to the Reviewer subagent default when unset."
          modelField="default_grouping_model"
          effortField="default_grouping_reasoning_effort"
          inheritLabel="Reviewer subagent default"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Open SWE Review Chat"
          description="Model used by the 'chat with this PR' assistant on the review page. Falls back to the Agent default when unset."
          modelField="default_chat_model"
          effortField="default_chat_reasoning_effort"
          inheritLabel="Agent default"
        />
      </div>
      <SaveError settings={settings} />
    </SettingsSection>
  )
}

interface ModelPairControlProps {
  models: Array<ModelOption>
  model: string | null
  effort: string | null
  onChange: (model: string, effort: string) => void
  disabled: boolean
  /**
   * When set, the model dropdown gains a leading "unset" option with this
   * label. Selecting it calls {@link onInherit}; an unset `model` renders as
   * this option.
   */
  inheritLabel?: string
  onInherit?: () => void
}

const INHERIT_VALUE = "__inherit__"

/** A model and reasoning-effort pair. */
function ModelPairControl({
  models,
  model,
  effort,
  onChange,
  disabled,
  inheritLabel,
  onInherit,
}: ModelPairControlProps) {
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
  )
}
