import { createFileRoute } from "@tanstack/react-router"

import { ConnectionsPage } from "@/features/settings/components/ConnectionsPage"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents/connections")({
  component: AppConnections,
  head: () => ({ meta: [{ title: pageTitle("Connections") }] }),
})

function AppConnections() {
  const { data: user } = useSession()
  return user ? <ConnectionsPage user={user} /> : null
}
