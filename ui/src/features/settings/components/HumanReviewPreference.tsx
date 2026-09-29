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
      {(profile.error || options.error) && (
        <p role="alert" className="px-4 py-2 text-xs text-destructive">
          Could not load the human review preference. Please try again.
        </p>
      )}
    </>
  )
}
