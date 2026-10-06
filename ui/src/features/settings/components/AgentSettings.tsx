import {
  SettingRow,
  SettingSection,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@langchain/gtm-platform-design-system/ui/select"
import { ProfileSwitchRow, useProfileSettings } from "./ProfileSwitchRow"
import { ModelPairControl } from "./WorkspaceSettingsSections"

const ROUTING: Record<string, { label: string; value: boolean | null }> = {
  inherit: { label: "Workspace default", value: null },
  enabled: { label: "On", value: true },
  disabled: { label: "Off", value: false },
}
const ROUTING_ITEMS = Object.entries(ROUTING).map(([value, { label }]) => ({
  value,
  label,
}))

function routingChoice(enabled: boolean | null | undefined): string {
  if (enabled == null) return "inherit"
  return enabled ? "enabled" : "disabled"
}

export function AgentSettings() {
  const { profile, options, ready, save } = useProfileSettings()
  const models = (options?.models ?? []).filter(
    (model) => model.can_be_default !== false
  )

  return (
    <>
      <SettingSection title="Models" contained>
        <SettingRow
          label="Adaptive routing"
          description="Pick a model for each turn automatically."
          control={(slot) => (
            <Select
              items={ROUTING_ITEMS}
              value={routingChoice(profile?.model_routing_enabled)}
              onValueChange={(v) =>
                v && save({ model_routing_enabled: ROUTING[v]?.value ?? null })
              }
              disabled={!ready}
            >
              <SelectTrigger
                id={slot.id}
                aria-describedby={slot.describedById}
                className="w-full"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROUTING_ITEMS.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        />
        <SettingRow
          label="Default model"
          description="Model and reasoning effort when adaptive routing is off."
          control={(slot) => (
            <ModelPairControl
              id={slot.id}
              describedBy={slot.describedById}
              models={models}
              model={
                profile?.default_model ?? options?.default_agent_model ?? null
              }
              effort={
                profile?.reasoning_effort ??
                options?.default_agent_reasoning_effort ??
                null
              }
              onChange={(model, effort) =>
                save({ default_model: model, reasoning_effort: effort })
              }
              disabled={!ready}
            />
          )}
        />
        <SettingRow
          label="Subagent model"
          description="Model and reasoning effort for delegated tasks."
          control={(slot) => (
            <ModelPairControl
              id={slot.id}
              describedBy={slot.describedById}
              models={models}
              model={profile?.default_subagent_model ?? null}
              effort={profile?.subagent_reasoning_effort ?? null}
              onChange={(model, effort) =>
                save({
                  default_subagent_model: model,
                  subagent_reasoning_effort: effort,
                })
              }
              inheritLabel="Same as default"
              onInherit={() =>
                save({
                  default_subagent_model: null,
                  subagent_reasoning_effort: null,
                })
              }
              disabled={!ready}
            />
          )}
        />
      </SettingSection>

      <SettingSection title="Runs" contained>
        <ProfileSwitchRow
          field="recent_thread_context_enabled"
          label="Include recent work"
          description="Give runs a filtered digest of your recent threads."
        />
        <ProfileSwitchRow
          field="preserve_sandbox_memory"
          label="Keep sandbox memory on auto-stop"
          description="Idle sandboxes save running processes so the next run resumes there. New sandboxes only."
          fallback
        />
      </SettingSection>
    </>
  )
}
