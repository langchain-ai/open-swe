import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function BackgroundCallbacksPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <>
      <SettingsRow
        label="Background command callbacks (experimental)"
        htmlFor="experimental-background-callbacks"
        description="Background commands you start report completion from the sandbox instead of being polled every minute."
        control={
          <Switch
            id="experimental-background-callbacks"
            checked={profile.data?.experimental_background_callbacks ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { experimental_background_callbacks: value },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the background command preference. Please try again.
        </p>
      )}
    </>
  )
}
