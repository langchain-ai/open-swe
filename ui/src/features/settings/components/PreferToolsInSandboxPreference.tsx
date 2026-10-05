import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function PreferToolsInSandboxPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <>
      <SettingsRow
        label="Prefer tools through the sandbox"
        htmlFor="prefer-tools-in-sandbox"
        description="The agent calls MCP integrations and large-result lookups through the sandbox tools endpoint instead of loading them as tools, so it can filter their output. Applies to threads you start after turning this on; existing threads keep their mode."
        control={
          <Switch
            id="prefer-tools-in-sandbox"
            checked={profile.data?.prefer_tools_in_sandbox ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { prefer_tools_in_sandbox: value },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the tools-in-sandbox preference. Please try again.
        </p>
      )}
    </>
  )
}
