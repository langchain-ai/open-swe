import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { PersonalInstructionsSection } from "@/features/settings/components/PersonalInstructionsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/instructions")({
  component: () => (
    <SettingsPage fill>
      <PersonalInstructionsSection />
    </SettingsPage>
  ),
  head: () => ({ meta: [{ title: pageTitle("Instructions") }] }),
})
