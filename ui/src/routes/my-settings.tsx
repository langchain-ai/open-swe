import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { GeneralSettings } from "@/features/settings/components/GeneralSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings")({
  component: () => (
    <SettingsPage
      title="General"
      description="How the dashboard looks and behaves for you."
    >
      <GeneralSettings />
    </SettingsPage>
  ),
  head: () => ({ meta: [{ title: pageTitle("General") }] }),
})
