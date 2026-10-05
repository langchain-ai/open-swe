import { SettingsRow } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { useOptions, usePatchProfile, useProfile } from "@/lib/profile"

export function HumanReviewPreference() {
  const profile = useProfile()
  const options = useOptions()
  const save = usePatchProfile()
  const defaults = options.data
  const disabled = !profile.isSuccess || !defaults

  return (
    <>
      <SettingsRow
        label="Link to Open SWE reviews in Slack"
        htmlFor="pr-review-links"
        description="When Open SWE posts pull request links for you in Slack, open the Open SWE review page instead of GitHub."
        control={
          <Switch
            id="pr-review-links"
            checked={profile.data?.pr_review_links ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { pr_review_links: value },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      <SettingsRow
        label="React to failing PR checks in Slack"
        htmlFor="pr-failure-reactions"
        description="Add ❌ to watched pull request posts in Slack when checks fail on a PR you own. Off by default."
        control={
          <Switch
            id="pr-failure-reactions"
            checked={profile.data?.pr_failure_reactions ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { pr_failure_reactions: value },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the human review preference. Please try again.
        </p>
      )}
    </>
  )
}
