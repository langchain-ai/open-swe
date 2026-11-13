import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { MCPConnectionsSection } from "@/features/settings/components/MCPConnectionsSection"
import {
  ManagedToolsSection,
  managedToolsQuery,
} from "@/features/settings/components/ManagedToolsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/connections")({
  component: ConnectionsPage,
  head: () => ({ meta: [{ title: pageTitle("Connections") }] }),
})

function ConnectionsPage() {
  const managed = useQuery(managedToolsQuery)
  return (
    <SettingsPage
      title="Connections"
      description="Accounts and tools Robin can use on your behalf in your private threads."
    >
      {(user) => (
        <>
          <ConnectionsSection user={user} />
          <ManagedToolsSection />
          <MCPConnectionsSection
            scope="user"
            addInLangSmith={managed.data?.configured === true}
          />
        </>
      )}
    </SettingsPage>
  )
}
