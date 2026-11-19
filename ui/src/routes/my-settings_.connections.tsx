import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { ConnectionsPage } from "@/features/settings/components/ConnectionsPage"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/connections")({
  component: SettingsConnections,
  head: () => ({ meta: [{ title: pageTitle("Connections") }] }),
})

function SettingsConnections() {
  return (
    <SettingsPage fill>
      {(user) => <ConnectionsPage user={user} />}
    </SettingsPage>
  )
}
