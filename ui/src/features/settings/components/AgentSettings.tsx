import { SettingsRow, SettingsSection } from "@/components/AppShell"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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
      <SettingsSection title="Models">
        <SettingsRow
          label="Adaptive routing"
          description="Pick a model for each turn automatically."
          control={
            <Select
              items={ROUTING_ITEMS}
              value={routingChoice(profile?.model_routing_enabled)}
              onValueChange={(v) =>
                v && save({ model_routing_enabled: ROUTING[v]?.value ?? null })
              }
              disabled={!ready}
            >
              <SelectTrigger className="w-40">
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
          }
        />
        <SettingsRow
          label="Default model"
          description="Model and reasoning effort when adaptive routing is off."
          control={
            <ModelPairControl
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
          }
        />
        <SettingsRow
          label="Subagent model"
          description="Model and reasoning effort for delegated tasks."
          control={
            <ModelPairControl
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
          }
        />
      </SettingsSection>

      <SettingsSection title="Runs">
        <ProfileSwitchRow
          field="recent_thread_context_enabled"
          label="Include recent work"
          description="Give runs a filtered digest of your recent threads."
        />
        <ProfileSwitchRow
          field="preserve_sandbox_memory"
          label="Keep sandbox memory on auto-stop"
          description="When an idle sandbox stops, save its running processes so the next run resumes where it left off. Applies to new sandboxes."
          fallback
        />
      </SettingsSection>
    </>
  )
}
