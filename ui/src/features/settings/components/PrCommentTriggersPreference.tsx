import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function PrCommentTriggersPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <>
      <SettingsRow
        label="Respond to PR comments without a mention"
        htmlFor="experimental-pr-comment-triggers"
        description="On pull requests Open SWE opens for you, comments and reviews from Open SWE users wake the agent without tagging it. Applies to threads you start after turning it on."
        control={
          <Switch
            id="experimental-pr-comment-triggers"
            checked={profile.data?.experimental?.pr_comment_triggers ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { experimental: { pr_comment_triggers: value } },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the PR comment preference. Please try again.
        </p>
      )}
    </>
  )
}
