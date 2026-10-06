import { createFileRoute } from "@tanstack/react-router"

import { SettingSection } from "@langchain/gtm-platform-design-system/patterns/setting-section"

import { SettingsPage } from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { MCPConnectionsSection } from "@/features/settings/components/MCPConnectionsSection"
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
          <SettingSection title="Slack" contained>
            <ProfileSwitchRow
              field="concierge_mode"
              label="Concierge mode"
              description="Your whole DM with Open SWE becomes one private thread it always answers in, instead of a new thread per message."
            />
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
          </SettingSection>
          <MCPConnectionsSection scope="user" />
        </>
      )}
    </SettingsPage>
  )
}
