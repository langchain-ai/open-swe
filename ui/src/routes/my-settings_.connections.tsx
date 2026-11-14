import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { ManagedToolsSection } from "@/features/settings/components/ManagedToolsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/connections")({
  component: ConnectionsPage,
  head: () => ({ meta: [{ title: pageTitle("Connections") }] }),
})

function ConnectionsPage() {
  return (
    <SettingsPage
      title="Connections"
      description="Accounts and tools Robin can use on your behalf in your private threads."
    >
      {(user) => (
        <>
          <ConnectionsSection user={user} />
          <ManagedToolsSection />
        </>
      )}
    </SettingsPage>
  )
}
