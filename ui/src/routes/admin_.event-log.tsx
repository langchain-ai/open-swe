import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { EventLog } from "@/features/settings/components/EventLog"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/event-log")({
  head: () => ({ meta: [{ title: pageTitle("Event log") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Event Log"
      description="Inbound GitHub, Slack, Linear and deployment deliveries and Robin's own events from the last 14 days."
      className="max-w-6xl"
    >
      <EventLog />
    </SettingsPage>
  ),
})
