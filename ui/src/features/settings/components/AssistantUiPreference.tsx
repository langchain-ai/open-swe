import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsRow } from "@/components/AppShell"
import {
  useExperimentalAssistantUi,
  usePatchProfile,
  useProfile,
} from "@/lib/profile"

import {
  setUseStreamPreference,
  useStreamPreference,
} from "@/lib/streamPreference"

export function AssistantUiPreference() {
  const profile = useProfile()
  const save = usePatchProfile()
  const enabled = useExperimentalAssistantUi()
  const preferStream = useStreamPreference()
  const disabled = !profile.isSuccess

  return (
    <>
      <SettingsRow
        label="SDK useStream (experimental)"
        htmlFor="sdk-use-stream"
        description="Use SDK streaming instead of the transcript with the same conversation UI. Applies only to this browser; legacy threads always use SDK streaming."
        control={
          <Switch
            id="sdk-use-stream"
            aria-label="SDK useStream (experimental)"
            checked={preferStream}
            onChange={setUseStreamPreference}
          />
        }
      />
      <SettingsRow
        label="Assistant UI (experimental)"
        htmlFor="experimental-assistant-ui"
        description="Use the new conversation interface for your account."
        control={
          <Switch
            id="experimental-assistant-ui"
            aria-label="Assistant UI (experimental)"
            checked={enabled}
            disabled={disabled}
            onChange={(value) => {
              save.patch({ experimental_assistant_ui: value })
            }}
          />
        }
      />
      {profile.error && (
        <p
          role="alert"
          className="px-space-4 py-space-2 text-xs text-error-secondary"
        >
          Could not load the conversation preference. Please try again.
        </p>
      )}
    </>
  )
}
