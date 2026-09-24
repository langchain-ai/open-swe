import { createFileRoute } from "@tanstack/react-router"

import { AuthedAppShell } from "@/components/AppShell"
import { AboutSection } from "@/features/settings/components/AboutSection"
import { AccountSection } from "@/features/settings/components/AccountSection"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { MCPConnectionsSection } from "@/features/settings/components/MCPConnectionsSection"
import { PersonalInstructionsSection } from "@/features/settings/components/PersonalInstructionsSection"
import { PreferencesSection } from "@/features/settings/components/PreferencesSection"
import { PullRequestsSection } from "@/features/settings/components/PullRequestsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings")({
  component: MySettingsPage,
  head: () => ({ meta: [{ title: pageTitle("Profile") }] }),
})

function MySettingsPage() {
  return (
    <AuthedAppShell
      title="Settings"
      description="Personal preferences, connected accounts, and instructions that apply to every run you trigger."
    >
      {(user) => (
        <>
          <AccountSection user={user} />
          <PreferencesSection />
          <PullRequestsSection />
          <ConnectionsSection user={user} />
          <MCPConnectionsSection scope="user" />
          <PersonalInstructionsSection />
          <AboutSection user={user} />
        </>
      )}
    </AuthedAppShell>
  )
}
