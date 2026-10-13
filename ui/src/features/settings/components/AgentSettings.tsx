import { SettingsSection } from "@/components/AppShell"
import { ProfileSwitchRow } from "./ProfileSwitchRow"

export function AgentSettings() {
  return (
    <SettingsSection title="Runs">
      <ProfileSwitchRow
        field="recent_thread_context_enabled"
        label="Include recent work"
        description="Give runs a filtered digest of your recent threads."
      />
      <ProfileSwitchRow
        field="auto_human_review_enabled"
        label="Allow automatic review picks"
        description="Let Open SWE pick you for human reviews. People can still name you explicitly when this is off."
        fallback
      />
    </SettingsSection>
  )
}
