import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { IncidentSettings } from "@/features/incidents/IncidentSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/incidents")({
  head: () => ({ meta: [{ title: pageTitle("Incidents") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Incidents"
      description="How Open SWE responds to incidents."
    >
      <IncidentSettings />
    </SettingsPage>
  ),
})
