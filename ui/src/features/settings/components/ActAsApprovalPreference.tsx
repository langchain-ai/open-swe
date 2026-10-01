import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function ActAsApprovalPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults
  const alwaysAllowed = profile.data?.act_as_always_allowed ?? false

  return (
    <>
      <SettingsRow
        label="Approve PRs opened as you"
        htmlFor="experimental-act-as-approval"
        description={
          alwaysAllowed
            ? "You chose Always allow, so Open SWE is not asking. Switch this off and on to be asked again."
            : "In Slack threads with more than one person, Open SWE DMs you for approval before opening a PR under your name."
        }
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
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the PR approval preference. Please try again.
        </p>
      )}
    </>
  )
}
