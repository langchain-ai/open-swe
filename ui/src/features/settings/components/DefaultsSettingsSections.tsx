"use client"

import { useEffect, useState } from "react"
import type { ModelOption, Repository, RobinSettings } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { ModelSelect } from "@/components/ModelSelect"
import { Select } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import {
  useInstanceSettings,
  type InstanceSettings,
  type StringSettingField,
} from "@/features/settings/lib/settingsScope"

export function DefaultRepoSection({
  repositories,
}: {
  /** Every repository the installation can see. */
  repositories: Array<Repository>
}) {
  const settings = useInstanceSettings()
  return (
    <SettingsSection
      title="Default Repository"
      description="Default repository when a run does not name one."
    >
      <div className="divide-y divide-default">
        <SettingsRow
          label="Repository"
          description="Used when a run names no repository and the user has no profile default."
          control={
            <div className="w-56">
              <RepoSelector
                repos={repositories}
                allowArchived
                selectedRepo={settings.data?.default_repo ?? null}
                onRepoChange={(repo) => settings.save({ default_repo: repo })}
                placeholder="Pick a repository…"
                emptySelectionLabel="No default repository"
                triggerClassName="h-7 w-full max-w-none rounded-md border border-default bg-surface-level-1 px-space-2 py-space-1 text-xs text-primary transition-colors hover:bg-surface-level-1-hover"
                disabled={!settings.data}
              />
            </div>
          }
        />
      </div>
    </SettingsSection>
  )
}

interface ModelRowProps {
  settings: InstanceSettings
  models: Array<ModelOption>
  label: string
  description: string
  modelField: StringSettingField
  effortField: StringSettingField
}

function ModelRow({
  settings,
  models,
  label,
  description,
  modelField,
  effortField,
}: ModelRowProps) {
  const patch = (model: string | null, effort: string | null) => {
    const next: Partial<RobinSettings> = {}
    next[modelField] = model
    next[effortField] = effort
    return next
  }
  return (
    <SettingsRow
      label={label}
      description={
        modelField === "default_agent_subagent_model"
          ? `${description} When this matches the main agent's model, effort follows the parent run. Saved effort applies only to a different model.`
          : description
      }
      control={
        <ModelPairControl
          label={label}
          models={models}
          model={settings.data?.[modelField] ?? null}
          effort={settings.data?.[effortField] ?? null}
          onChange={(model, effort) => settings.save(patch(model, effort))}
          disabled={!settings.data}
          parentModel={
            modelField === "default_agent_subagent_model"
              ? settings.data?.default_agent_model
              : undefined
          }
        />
      }
    />
  )
}

export function ModelDefaultsSection({
  models,
}: {
  models: Array<ModelOption>
}) {
  const settings = useInstanceSettings()

  return (
    <SettingsSection
      title="Model Defaults"
      description="Default models for runs. Each user's Agent settings override the agent defaults."
    >
      <div className="divide-y divide-default">
        <ModelRow
          settings={settings}
          models={models}
          label="Robin Agent"
          description="Model used for code-writing runs triggered from Slack, Linear, GitHub, and the Robin Agent."
          modelField="default_agent_model"
          effortField="default_agent_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Robin Agent subagents"
          description="Model used by delegated main-agent tasks."
          modelField="default_agent_subagent_model"
          effortField="default_agent_subagent_reasoning_effort"
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
          label="Robin Reviewer"
          description="Model used for PR review runs."
          modelField="default_reviewer_model"
          effortField="default_reviewer_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Robin Reviewer subagents"
          description="Model used by delegated reviewer tasks."
          modelField="default_reviewer_subagent_model"
          effortField="default_reviewer_subagent_reasoning_effort"
        />
        <ModelRow
          settings={settings}
          models={models}
          label="Review scout"
          description="Model used to plan shared PR walkthroughs and summarize human input."
          modelField="default_review_scout_model"
          effortField="default_review_scout_reasoning_effort"
        />
      </div>
    </SettingsSection>
  )
}

interface ModelPairControlProps {
  /** Names the pickers for assistive tech, e.g. "Open SWE Agent model". */
  label: string
  models: Array<ModelOption>
  model: string | null
  effort: string | null
  onChange: (model: string, effort: string) => void
  disabled: boolean
  parentModel?: string | null
}

const INHERIT_VALUE = "__inherit__"

/** A model and reasoning-effort pair. */
export function ModelPairControl({
  label,
  models,
  model,
  effort,
  onChange,
  disabled,
  parentModel,
}: ModelPairControlProps) {
  const [localModel, setLocalModel] = useState<string>(model ?? "")
  const [localEffort, setLocalEffort] = useState<string>(effort ?? "")

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setLocalModel(model ?? "")
    setLocalEffort(effort ?? "")
  }, [model, effort])

  const inheritsParentEffort = Boolean(
    parentModel && localModel === parentModel
  )
  const selectedModel = models.find((m) => m.id === localModel)
  const availableEfforts = selectedModel?.efforts ?? []

  const handleModelChange = (value: string | undefined) => {
    if (!value) return
    const nextModel = models.find((m) => m.id === value)
    if (!nextModel) return
    const nextEffort = nextModel.efforts.includes(localEffort)
      ? localEffort
      : nextModel.default_effort
    setLocalModel(value)
    setLocalEffort(nextEffort)
    onChange(value, nextEffort)
  }

  const handleEffortChange = (value: string | undefined) => {
    if (!value || !localModel) return
    setLocalEffort(value)
    onChange(localModel, value)
  }

  return (
    <div className="flex items-center gap-space-2">
      <ModelSelect
        aria-label={`${label} model`}
        models={models}
        variant="outlined"
        value={localModel || undefined}
        onChange={handleModelChange}
        disabled={disabled}
        triggerClassName="w-40"
      />
      <Select
        aria-label={`${label} reasoning effort`}
        placeholder="effort"
        options={
          inheritsParentEffort
            ? [{ value: INHERIT_VALUE, label: "Parent run" }]
            : availableEfforts.map((e) => ({ value: e, label: e }))
        }
        value={inheritsParentEffort ? INHERIT_VALUE : localEffort || undefined}
        onChange={handleEffortChange}
        disabled={disabled || !localModel || inheritsParentEffort}
        triggerClassName="w-28"
      />
    </div>
  )
}

type RoutingMode = "off" | "adaptive" | "split"

const ROUTING_MODES = [
  { value: "off", label: "Off" },
  { value: "adaptive", label: "Adaptive" },
  { value: "split", label: "50/50 split (v2)" },
] satisfies Array<{ value: RoutingMode; label: string }>

function routingMode(settings: RobinSettings | undefined): RoutingMode {
  if (!(settings?.model_routing_enabled ?? true)) return "off"
  return settings?.model_routing_split ? "split" : "adaptive"
}

const ROUTING_PATCHES: Record<RoutingMode, Partial<RobinSettings>> = {
  off: { model_routing_enabled: false },
  adaptive: { model_routing_enabled: true, model_routing_split: false },
  split: { model_routing_enabled: true, model_routing_split: true },
}

export function ModelRoutingSection({
  models,
}: {
  models: Array<ModelOption>
}) {
  const settings = useInstanceSettings()
  return (
    <SettingsSection
      title="Model Routing"
      description="How a thread on Auto picks its model. A model someone picks in the composer, or asks for in a message, always wins, and automations, API clients and Slack bots run the Robin Agent model."
    >
      <div className="divide-y divide-default">
        <SettingsRow
          label="Routing"
          description="Off runs the Robin Agent model. Adaptive has the classifier pick a tier for each thread. The 50/50 split runs half of threads on Balanced and half on Performance, by thread."
          control={
            <Select
              aria-label="Routing"
              options={ROUTING_MODES}
              value={routingMode(settings.data)}
              onChange={(next) => {
                if (next) settings.save(ROUTING_PATCHES[next])
              }}
              disabled={!settings.data}
              triggerClassName="w-48"
            />
          }
        />
        <SettingsRow
          label="Allow model selection in thread composers"
          description="Off hides the model picker and uses Auto for new threads. Existing thread selections and requests in conversation or other surfaces remain available."
          control={
            <Switch
              aria-label="Allow model selection in thread composers"
              checked={settings.data?.model_selection_enabled === true}
              onChange={(next) =>
                settings.save({ model_selection_enabled: next })
              }
              disabled={!settings.data}
            />
          }
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
          description="Model used for complex reasoning."
          modelField="default_agent_routing_performance_model"
          effortField="default_agent_routing_performance_reasoning_effort"
        />
      </div>
    </SettingsSection>
  )
}
