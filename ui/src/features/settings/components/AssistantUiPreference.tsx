import {
  SettingRow,
  SettingSection,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import { AlertTriangle } from "@/components/glyphs"
import {
  useExperimentalAssistantUi,
  useOptions,
  usePatchProfile,
  useProfile,
} from "@/lib/profile"

import {
  setUseStreamPreference,
  useStreamPreference,
} from "@/lib/streamPreference"

/** The Interface experiments: conversation transport and the assistant UI. */
export function AssistantUiPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const enabled = useExperimentalAssistantUi()
  const preferStream = useStreamPreference()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <>
      {(profile.error || options.error) && (
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title="Could not load the conversation preference"
          description="The Assistant UI switch is disabled until it loads. Please try again."
        />
      )}
      <SettingSection title="Interface" contained>
        <SettingRow
          density="compact"
          label="SDK useStream (experimental)"
          description="Stream with the SDK instead of the transcript, same UI. This browser only; legacy threads always do."
          control={(slot) => (
            <Switch
              id={slot.id}
              aria-describedby={slot.describedById}
              checked={preferStream}
              onCheckedChange={setUseStreamPreference}
            />
          )}
        />
        <SettingRow
          density="compact"
          label="Assistant UI (experimental)"
          description="Use the new conversation interface for your account."
          control={(slot) => (
            <Switch
              id={slot.id}
              aria-describedby={slot.describedById}
              checked={enabled}
              disabled={disabled}
              onCheckedChange={(value) => {
                if (!defaults) return
                save.patch(
                  { experimental_assistant_ui: value },
                  defaults.default_agent_model,
                  defaults.default_agent_reasoning_effort
                )
              }}
            />
          )}
        />
      </SettingSection>
    </>
  )
}
