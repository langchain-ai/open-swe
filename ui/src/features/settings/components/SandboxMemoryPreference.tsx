import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function SandboxMemoryPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <>
      <SettingsRow
        label="Keep sandbox memory on auto-stop"
        htmlFor="preserve-sandbox-memory"
        description="When an idle sandbox stops, save its running processes so the next run resumes where it left off. Applies to sandboxes created after you turn this on."
        control={
          <Switch
            id="preserve-sandbox-memory"
            checked={profile.data?.preserve_sandbox_memory ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { preserve_sandbox_memory: value },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the sandbox memory preference. Please try again.
        </p>
      )}
    </>
  )
}
