import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage, SettingsSection } from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { MCPConnectionsSection } from "@/features/settings/components/MCPConnectionsSection"
import { ManagedToolsSection } from "@/features/settings/components/ManagedToolsSection"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/connections")({
  component: ConnectionsPage,
  head: () => ({ meta: [{ title: pageTitle("Connections") }] }),
})

function ConnectionsPage() {
  return (
    <SettingsPage
      title="Connections"
      description="Accounts and tools Open SWE can use on your behalf in your private threads."
    >
      {(user) => (
        <>
          <ConnectionsSection user={user} />
          <ManagedToolsSection />
          <SettingsSection title="Slack">
            <ProfileSwitchRow
              field="pr_review_links"
              label="Open pull requests in Open SWE"
              description="Pull request links Open SWE posts for you open its review page instead of GitHub."
            />
            <ProfileSwitchRow
              field="pr_failure_reactions"
              label="React to failing checks"
              description="Add ❌ to watched pull request posts when checks fail on a pull request you own."
            />
          </SettingsSection>
          <MCPConnectionsSection scope="user" />
        </>
      )}
    </SettingsPage>
  )
}
