import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { PluginsSettings } from "@/features/plugins/components/PluginsSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/plugins")({
  component: PluginsPage,
  head: () => ({ meta: [{ title: pageTitle("Plugins") }] }),
})

function PluginsPage() {
  return (
    <SettingsPage
      title="Plugins"
      description="Add pages to your sidebar from GitHub repositories."
    >
      <PluginsSettings />
    </SettingsPage>
  )
}
