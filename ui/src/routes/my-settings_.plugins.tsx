import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { PluginsSettings } from "@/features/plugins/components/PluginsSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/plugins")({
  component: PluginsPage,
  head: () => ({ meta: [{ title: pageTitle("Apps") }] }),
})

function PluginsPage() {
  return (
    <SettingsPage
      title="Apps"
      description="Apps an agent built for you, or that others listed, in your sidebar."
    >
      <PluginsSettings />
    </SettingsPage>
  )
}
