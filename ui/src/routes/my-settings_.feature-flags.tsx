import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage, SettingsSection } from "@/components/AppShell"
import { StreamPreference } from "@/features/settings/components/StreamPreference"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/feature-flags")({
  component: FeatureFlagsPage,
  head: () => ({ meta: [{ title: pageTitle("Feature flags") }] }),
})

function FeatureFlagsPage() {
  return (
    <SettingsPage
      title="Feature flags"
      description="Features under test. They may change or go away."
    >
      <SettingsSection title="Interface">
        <StreamPreference />
      </SettingsSection>
      <SettingsSection title="Slack">
        <ProfileSwitchRow
          field="concierge_mode"
          label="Concierge mode"
          description="Your whole DM with Robin becomes one private thread it always answers in, instead of a new thread per message."
        />
      </SettingsSection>
      <SettingsSection title="Agent">
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
        />
      </SettingsSection>
    </SettingsPage>
  )
}
