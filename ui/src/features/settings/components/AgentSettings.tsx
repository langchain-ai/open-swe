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
        field="preserve_sandbox_memory"
        label="Keep sandbox memory on auto-stop"
        description="When an idle sandbox stops, save its running processes so the next run resumes where it left off. Applies to new sandboxes."
        fallback
      />
    </SettingsSection>
  )
}
