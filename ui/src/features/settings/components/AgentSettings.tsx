import { SettingsSection } from "@/components/AppShell"
import { ProfileSwitchRow } from "./ProfileSwitchRow"

export function AgentSettings() {
  return (
    <SettingsSection title="Runs">
      <ProfileSwitchRow
        field="auto_human_review_enabled"
        label="Allow automatic review picks"
        description="Let Robin pick you for human reviews. People can still name you explicitly when this is off."
        fallback
      />
      <ProfileSwitchRow
        field="github_review_request_comments"
        label="GitHub review-request comments"
        description="Post a GitHub comment linking to Slack when you request a review. Turning this off leaves existing comments unchanged."
      />
    </SettingsSection>
  )
}
