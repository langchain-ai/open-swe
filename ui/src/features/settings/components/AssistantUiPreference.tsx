import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import {
  buildProfileUpdate,
  useExperimentalAssistantUi,
  useOptions,
  useProfile,
  useSaveProfile,
} from "@/lib/profile"

export function AssistantUiPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = useSaveProfile()
  const enabled = useExperimentalAssistantUi()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults || save.isPending

  return (
    <>
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
              if (!defaults) return
              save.mutate(
                buildProfileUpdate(
                  profile.data,
                  { experimental_assistant_ui: value },
                  defaults.default_agent_model,
                  defaults.default_agent_reasoning_effort
                )
              )
            }}
          />
        }
      />
      {(save.error || profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not {save.error ? "save" : "load"} the conversation preference.
          Please try again.
        </p>
      )}
    </>
  )
}
