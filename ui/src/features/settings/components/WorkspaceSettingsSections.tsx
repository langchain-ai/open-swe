"use client"

import { useEffect, useState, type ReactNode } from "react"
import {
  SettingRow,
  SettingSection,
  type SettingControlSlot,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@langchain/gtm-platform-design-system/ui/select"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import type { ModelOption, Repository, WorkspaceSettings } from "@/lib/api"
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
 * The reset rides with that statement beside the label, so the control lane
 * keeps its one control.
 */
export function TierRow({
  settings,
  fields,
  label,
  description,
  density,
  tag,
  control,
}: {
  settings: ScopedSettings
  fields: Array<keyof WorkspaceSettings>
  label: string
  description?: string
  density?: "default" | "compact"
  /** A statement about the setting itself, such as Experimental. */
  tag?: ReactNode
  control: (slot: SettingControlSlot) => ReactNode
}) {
  const scoped = settings.scope.kind === "workspace"
  const inherits = settings.inherits(...fields)
  return (
    <SettingRow
      label={label}
      description={description}
      density={density}
      badge={
        scoped || tag ? (
          <Inline gap="xs" align="center">
            {tag}
            {scoped && (
              <Badge tier="quiet" tone={inherits ? "neutral" : "attention"}>
                {inherits ? "Inherited" : "Overridden"}
              </Badge>
            )}
            {scoped && !inherits && (
              <Button
                size="compact"
                variant="ghost"
                onClick={() => settings.reset(...fields)}
                aria-label={`Reset ${label} to the instance value`}
              >
                Reset
              </Button>
            )}
          </Inline>
        ) : undefined
      }
      control={control}
    />
  )
}

export function LLMGatewaySection({ scope }: { scope: SettingsScope }) {
  const settings = useScopedSettings(scope)
  const mode = gatewayMode(settings.data?.gateway_enabled)
  const scoped = scope.kind === "workspace"
  const modes = [
    {
      value: "inherit",
      label: scoped ? "Inherit instance setting" : "Inherit deployment default",
    },
    { value: "enabled", label: "Enabled" },
    { value: "disabled", label: "Disabled" },
  ] satisfies Array<{ value: GatewayMode; label: string }>

  return (
    <SettingSection
      contained
      title="LLM Gateway"
      description="Route agent and reviewer LLM calls through the LangSmith LLM Gateway. It authenticates with the workspace LangSmith API key and resolves provider keys from Provider Secrets, so no provider keys are needed at runtime. OpenAI, Anthropic, Fireworks, and Google Gemini are routed; other providers call the provider directly. Requires the gateway (private beta) enabled for your organization."
    >
      <TierRow
        settings={settings}
        fields={["gateway_enabled"]}
        label="Route through the gateway"
        description={
          scoped
            ? "Inherit follows the instance setting."
            : "Inherit uses the LANGSMITH_GATEWAY_ENABLED deployment default."
        }
        control={(slot) => (
          <Select
            items={modes}
            value={mode}
            onValueChange={(next) => {
              const value = gatewayModeValue(next as GatewayMode)
              if (value === null && scoped) settings.reset("gateway_enabled")
              else settings.save({ gateway_enabled: value })
            }}
            disabled={!settings.data}
          >
            <SelectTrigger
              id={slot.id}
              aria-describedby={slot.describedById}
              className="w-48"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {modes.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      />
    </SettingSection>
  )
}

export function DefaultRepoSection({
  scope,
  repositories,
}: {
  scope: SettingsScope
  /** Every repository the installation can see: any workspace may default to any of them. */
  repositories: Array<Repository>
}) {
  const settings = useScopedSettings(scope)
  const scoped = scope.kind === "workspace"
  return (
    <SettingSection
      contained
      title="Default repository"
      description={
        scoped
          ? "Where a run in this workspace lands when nothing names a repository. Any repository the GitHub App can access will do."
          : "Where a run lands when nothing names a repository and the workspace sets no default of its own."
      }
    >
      <TierRow
        settings={settings}
        fields={["default_repo"]}
        label="Repository"
        description="Used when a run names no repository and the user has no profile default."
        control={() => (
          <RepoSelector
            appearance="field"
            className="w-full"
            repos={repositories}
            allowArchived
            selectedRepo={settings.data?.default_repo ?? null}
            onRepoChange={(repo) => settings.save({ default_repo: repo })}
            placeholder="Pick a repository…"
            emptySelectionLabel="No default repository"
            disabled={!settings.data}
          />
        )}
      />
    </SettingSection>
  )
}

interface ModelRowProps {
  settings: ScopedSettings
  models: Array<ModelOption>
  label: string
  description: string
  modelField: StringSettingField
  effortField: StringSettingField
  /**
   * Label of the picker's "unset" option on the instance, for roles that fall
   * back to another role there. On a workspace the same option only clears the
   * override, so it reads as inheritance instead: the instance may set a model.
   */
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
      control={(slot) => (
        <ModelPairControl
          id={slot.id}
          describedBy={slot.describedById}
          models={models}
          model={settings.data?.[modelField] ?? null}
          effort={settings.data?.[effortField] ?? null}
          onChange={(model, effort) => settings.save(patch(model, effort))}
          disabled={!settings.data}
          inheritLabel={
            inheritLabel && settings.scope.kind === "workspace"
              ? "Inherit instance setting"
              : inheritLabel
          }
          onInherit={
            inheritLabel
              ? () =>
                  settings.scope.kind === "instance"
                    ? settings.save(patch(null, null))
                    : settings.reset(modelField, effortField)
              : undefined
          }
        />
      )}
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
    <SettingSection
      contained
      title="Model defaults"
      description={
        scoped
          ? "Models for runs in this workspace. Rows marked Inherited follow the instance defaults; change one to override it here. Each user's Agent settings override the agent defaults. Review Chat unset here follows the instance setting, and only falls back to the Agent default when the instance leaves it unset too."
          : "Models for runs in every workspace that does not override them. Each user's Agent settings override the agent defaults."
      }
    >
      <TierRow
        settings={settings}
        fields={["model_routing_enabled"]}
        label="Adaptive model routing"
        description="Automatically choose a model for each turn. Users can still override this in their personal settings."
        density="compact"
        control={(slot) => (
          <Switch
            id={slot.id}
            aria-describedby={slot.describedById}
            checked={settings.data?.model_routing_enabled ?? false}
            onCheckedChange={(next) =>
              settings.save({ model_routing_enabled: next })
            }
            disabled={!settings.data}
          />
        )}
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
        description="Model used for complex reasoning."
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
        label="Open SWE Review Chat"
        description={
          scoped
            ? "Model used by the 'chat with this PR' assistant on the review page."
            : "Model used by the 'chat with this PR' assistant on the review page. Falls back to the Agent default when unset."
        }
        modelField="default_chat_model"
        effortField="default_chat_reasoning_effort"
        inheritLabel="Agent default"
      />
    </SettingSection>
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
  /** Lands on the model trigger, so a row's label names it. */
  id?: string
  describedBy?: string
}

const INHERIT_VALUE = "__inherit__"

/** A model and reasoning-effort pair. */
export function ModelPairControl({
  models,
  model,
  effort,
  onChange,
  disabled,
  inheritLabel,
  onInherit,
  id,
  describedBy,
}: ModelPairControlProps) {
  const modelItems = [
    ...(inheritLabel ? [{ value: INHERIT_VALUE, label: inheritLabel }] : []),
    ...models.map((m) => ({ value: m.id, label: m.label })),
  ]
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
    <Inline gap="sm" align="center">
      <Select
        items={modelItems}
        value={localModel}
        onValueChange={handleModelChange}
        disabled={disabled}
      >
        <SelectTrigger id={id} aria-describedby={describedBy} className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {modelItems.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={localEffort}
        onValueChange={handleEffortChange}
        disabled={disabled || !localModel || isInherit}
      >
        <SelectTrigger aria-label="Reasoning effort" className="w-28">
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
    </Inline>
  )
}
