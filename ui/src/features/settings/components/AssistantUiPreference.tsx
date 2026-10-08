import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
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
            checked={preferStream}
            onCheckedChange={setUseStreamPreference}
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
            checked={enabled}
            disabled={disabled}
            onCheckedChange={(value) => {
              save.patch({ experimental_assistant_ui: value })
            }}
          />
        }
      />
      {profile.error && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the conversation preference. Please try again.
        </p>
      )}
    </>
  )
}
