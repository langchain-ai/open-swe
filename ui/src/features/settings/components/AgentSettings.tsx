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
        field="review_request_dms_enabled"
        label="Slack notifications for review requests"
        description="Let Robin DM you in Slack when it picks you for a review, reminds you, or releases you. GitHub still requests your review when this is off."
        fallback
      />
    </SettingsSection>
  )
}
