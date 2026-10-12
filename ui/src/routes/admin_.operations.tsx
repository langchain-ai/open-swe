import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import {
  RunningAgentsSection,
  TriggerReviewSection,
} from "@/features/settings/components/AdminSections"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/operations")({
  head: () => ({ meta: [{ title: pageTitle("Operations") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Operations"
      description="Intervene in running agents and reviews across every workspace."
    >
      <RunningAgentsSection />
      <TriggerReviewSection />
    </SettingsPage>
  ),
})
