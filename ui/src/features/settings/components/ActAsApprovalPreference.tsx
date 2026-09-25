import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function ActAsApprovalPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <SettingsRow
      label="Approve PRs opened as you (experimental)"
      htmlFor="experimental-act-as-approval"
      description="In Slack threads with more than one person, Open SWE DMs you for approval before opening a PR under your name."
      control={
        <Switch
          id="experimental-act-as-approval"
          checked={profile.data?.experimental_act_as_approval ?? false}
          disabled={disabled}
          onCheckedChange={(value) => {
            if (!defaults) return
            save.patch(
              { experimental_act_as_approval: value },
              defaults.default_agent_model,
              defaults.default_agent_reasoning_effort
            )
          }}
        />
      }
    />
  )
}
