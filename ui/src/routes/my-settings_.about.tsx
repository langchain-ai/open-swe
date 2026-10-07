import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { AboutSection } from "@/features/settings/components/AboutSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/about")({
  component: () => (
    <SettingsPage
      title="About"
      description="Versions and diagnostics for support."
    >
      {(user) => <AboutSection user={user} />}
    </SettingsPage>
  ),
  head: () => ({ meta: [{ title: pageTitle("About") }] }),
})
