import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function McpToolsInSandboxPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <>
      <SettingsRow
        label="MCP tools only through the sandbox"
        htmlFor="mcp-tools-in-sandbox"
        description="The agent calls MCP integrations through the sandbox tools endpoint instead of loading them as tools. Applies to threads you start after turning this on; existing threads keep their mode."
        control={
          <Switch
            id="mcp-tools-in-sandbox"
            checked={profile.data?.mcp_tools_in_sandbox ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { mcp_tools_in_sandbox: value },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the MCP tools preference. Please try again.
        </p>
      )}
    </>
  )
}
