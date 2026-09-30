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
        label="Request human reviews in Slack"
        htmlFor="human-review-requests"
        description="Ask a repository's Slack review channel to review a pull request, from the dashboard or by asking Open SWE. The pull request merges once its reviewers approve."
        control={
          <Switch
            id="human-review-requests"
            checked={profile.data?.human_review_requests ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { human_review_requests: value },
                defaults.default_agent_model,
                defaults.default_agent_reasoning_effort
              )
            }}
          />
        }
      />
      <SettingsRow
        label="Watch pull requests I post for review"
        htmlFor="review-channel-watch"
        description="When you link a pull request in its repository's Slack review channel, Open SWE reacts once it is approved and once it merges. If it sits green without an approval for 30 minutes, Open SWE bumps it and picks a reviewer."
        control={
          <Switch
            id="review-channel-watch"
            checked={profile.data?.review_channel_watch ?? false}
            disabled={disabled}
            onCheckedChange={(value) => {
              if (!defaults) return
              save.patch(
                { review_channel_watch: value },
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
